// AnyLoc Bluetooth/iAP2 bridge.
//
// This program is our small adapter around an already-installed iAP2 runtime
// (libiOSBluetoothConnect.dylib). It does not contain or redistribute that
// library. The challenge signer is supplied separately through
// ANYLOC_BT_CERTIFY_CMD: the command receives a base64 challenge and prints a
// base64 (or hex) response.

#include <dlfcn.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace {

using CreateFn = void* (*)(uint64_t);
using ReleaseFn = void (*)(void**);

constexpr const char* DEFAULT_DYLIB =
    "/Applications/AnyGo.app/Contents/MacOS/libiOSBluetoothConnect.dylib";

struct Context {
    void* object = nullptr;
    std::string certify_cmd;
    std::mutex mutex;
    std::condition_variable cv;
    std::string challenge_b64;
    std::vector<unsigned char> response;
    std::atomic<bool> challenge_ready{false};
    std::atomic<bool> stop{false};
};

Context g_ctx;

std::string base64_encode(const unsigned char* data, int len) {
    static const char* alphabet =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    out.reserve(((len + 2) / 3) * 4);
    int i = 0;
    while (i + 2 < len) {
        unsigned int v = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2];
        out.push_back(alphabet[(v >> 18) & 63]);
        out.push_back(alphabet[(v >> 12) & 63]);
        out.push_back(alphabet[(v >> 6) & 63]);
        out.push_back(alphabet[v & 63]);
        i += 3;
    }
    if (i < len) {
        unsigned int v = data[i] << 16;
        out.push_back(alphabet[(v >> 18) & 63]);
        if (i + 1 < len) {
            v |= data[i + 1] << 8;
            out.push_back(alphabet[(v >> 12) & 63]);
            out.push_back(alphabet[(v >> 6) & 63]);
            out.push_back('=');
        } else {
            out.push_back(alphabet[(v >> 12) & 63]);
            out.push_back('=');
            out.push_back('=');
        }
    }
    return out;
}

int b64_value(char c) {
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52;
    if (c == '+') return 62;
    if (c == '/') return 63;
    return -1;
}

std::vector<unsigned char> base64_decode(const std::string& text) {
    std::vector<unsigned char> out;
    int val = 0;
    int bits = -8;
    for (char c : text) {
        if (c == '=') break;
        int d = b64_value(c);
        if (d < 0) continue;
        val = (val << 6) | d;
        bits += 6;
        if (bits >= 0) {
            out.push_back(static_cast<unsigned char>((val >> bits) & 0xFF));
            bits -= 8;
        }
    }
    return out;
}

std::vector<unsigned char> decode_response(const std::string& text) {
    std::string clean;
    for (char c : text) {
        if (std::isxdigit(static_cast<unsigned char>(c)) || c == ' ') clean.push_back(c);
    }
    bool looks_hex = clean.find(' ') != std::string::npos || clean.size() % 2 == 0;
    if (looks_hex) {
        std::vector<unsigned char> hex;
        std::istringstream in(clean);
        std::string token;
        while (in >> token) {
            if (token.size() != 2) { hex.clear(); break; }
            try {
                hex.push_back(static_cast<unsigned char>(std::stoul(token, nullptr, 16)));
            } catch (...) { hex.clear(); break; }
        }
        if (!hex.empty()) return hex;
    }
    return base64_decode(text);
}

std::string shell_quote(const std::string& value) {
    std::string out = "'";
    for (char c : value) {
        if (c == '\'') out += "'\\''";
        else out += c;
    }
    out += "'";
    return out;
}

void challenge_callback(const unsigned char* data, int len, int type,
                        unsigned char* output, int* output_len, void* user) {
    (void)output;
    (void)type;
    auto* ctx = static_cast<Context*>(user);
    if (ctx == nullptr || data == nullptr || len <= 0) {
        if (output_len) *output_len = 0;
        return;
    }
    std::string challenge = base64_encode(data, len);
    std::cerr << "[bt] challenge received: " << challenge << "\n";
    {
        std::lock_guard<std::mutex> lock(ctx->mutex);
        ctx->challenge_b64 = challenge;
        ctx->response.clear();
        ctx->challenge_ready = false;
    }

    if (ctx->certify_cmd.empty()) {
        std::cerr << "[bt] no ANYLOC_BT_CERTIFY_CMD configured\n";
        if (output_len) *output_len = 0;
        return;
    }

    std::string command = shell_quote(ctx->certify_cmd) + " " + shell_quote(challenge);
    FILE* pipe = popen(command.c_str(), "r");
    if (!pipe) {
        std::cerr << "[bt] failed to start certifier\n";
        if (output_len) *output_len = 0;
        return;
    }
    std::string result;
    char buffer[4096];
    while (fgets(buffer, sizeof(buffer), pipe) != nullptr) result += buffer;
    int rc = pclose(pipe);
    if (rc != 0) {
        std::cerr << "[bt] certifier returned " << rc << "\n";
        if (output_len) *output_len = 0;
        return;
    }
    auto decoded = decode_response(result);
    {
        std::lock_guard<std::mutex> lock(ctx->mutex);
        ctx->response = decoded;
        ctx->challenge_ready = true;
    }
    ctx->cv.notify_all();
    if (output_len) *output_len = 0;
    std::cerr << "[bt] certifier response: " << decoded.size() << " bytes\n";
}

uint64_t parse_mac(const std::string& text) {
    unsigned int bytes[6] = {};
    char sep = 0;
    std::istringstream in(text);
    for (int i = 0; i < 6; ++i) {
        std::string part;
        if (!std::getline(in, part, ':')) return 0;
        if (part.size() != 2) return 0;
        bytes[i] = static_cast<unsigned int>(std::stoul(part, nullptr, 16));
    }
    uint64_t value = 0;
    for (int i = 0; i < 6; ++i) value |= (uint64_t(bytes[i]) << (8 * i));
    (void)sep;
    return value;
}

std::vector<unsigned char> read_file(const std::string& path) {
    std::ifstream file(path, std::ios::binary);
    return std::vector<unsigned char>(std::istreambuf_iterator<char>(file), {});
}

void* load_runtime() {
    const char* path = std::getenv("ANYLOC_BT_DYLIB");
    if (!path || !*path) path = DEFAULT_DYLIB;
    void* handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!handle) std::cerr << "[bt] dlopen failed: " << dlerror() << "\n";
    return handle;
}

void* get_slot(void* object, int index) {
    auto* vptr = *reinterpret_cast<void***>(object);
    return vptr[index];
}

int open_channel(void* object) {
    using Fn = int (*)(void*);
    auto fn = reinterpret_cast<Fn>(get_slot(object, 2));
    for (int i = 0; i < 8; ++i) {
        int rc = fn(object);
        if (rc) return rc;
        std::this_thread::sleep_for(std::chrono::seconds(1));
    }
    return 0;
}

int connect_with_credentials(void* object, const std::vector<unsigned char>& cert,
                             const std::vector<unsigned char>& chip) {
    using SetRequestFn = void (*)(void*, void (*)(const unsigned char*, int, int,
                                                  unsigned char*, int*, void*), void*, int);
    using ConnectFn = int (*)(void*, const unsigned char*, int, const unsigned char*, int);
    using ChallengeResponseFn = int (*)(void*, const unsigned char*, int);

    auto set_request = reinterpret_cast<SetRequestFn>(get_slot(object, 6));
    set_request(object, challenge_callback, &g_ctx, 1);

    auto connect = reinterpret_cast<ConnectFn>(get_slot(object, 3));
    auto challenge_response = reinterpret_cast<ChallengeResponseFn>(get_slot(object, 4));

    for (int attempt = 0; attempt < 8 && !g_ctx.stop; ++attempt) {
        std::cerr << "[bt] iAP2 connect attempt " << (attempt + 1) << "\n";
        int rc = connect(object, cert.data(), static_cast<int>(cert.size()),
                         chip.data(), static_cast<int>(chip.size()));
        if (rc == 0) return 0;

        std::vector<unsigned char> response;
        {
            std::unique_lock<std::mutex> lock(g_ctx.mutex);
            g_ctx.cv.wait_for(lock, std::chrono::seconds(2), [&] { return g_ctx.challenge_ready.load(); });
            if (g_ctx.challenge_ready) response = g_ctx.response;
        }
        if (!response.empty()) {
            std::cerr << "[bt] sending challenge response\n";
            int rr = challenge_response(object, response.data(), static_cast<int>(response.size()));
            std::cerr << "[bt] challenge response rc=" << rr << "\n";
        }
    }
    return -1;
}

int run_probe(const std::string& mac_text) {
    void* handle = load_runtime();
    if (!handle) return 2;
    auto create = reinterpret_cast<CreateFn>(dlsym(handle, "CreateiOSBluetoothLocation"));
    auto release = reinterpret_cast<ReleaseFn>(dlsym(handle, "ReleaseiOSBluetoothLocation"));
    if (!create || !release) {
        std::cerr << "[bt] runtime symbols missing\n";
        dlclose(handle);
        return 2;
    }
    uint64_t mac = parse_mac(mac_text);
    if (!mac) {
        std::cerr << "[bt] invalid MAC\n";
        dlclose(handle);
        return 2;
    }
    void* object = create(mac);
    if (!object) {
        std::cerr << "[bt] CreateiOSBluetoothLocation failed\n";
        dlclose(handle);
        return 3;
    }
    int rc = open_channel(object);
    std::cerr << "[bt] OpenRfcommChannel rc=" << rc << "\n";
    release(&object);
    dlclose(handle);
    return rc ? 0 : 4;
}

int run_connect(const std::string& mac_text, const std::string& cert_path,
                const std::string& chip_path) {
    void* handle = load_runtime();
    if (!handle) return 2;
    auto create = reinterpret_cast<CreateFn>(dlsym(handle, "CreateiOSBluetoothLocation"));
    auto release = reinterpret_cast<ReleaseFn>(dlsym(handle, "ReleaseiOSBluetoothLocation"));
    if (!create || !release) {
        std::cerr << "[bt] runtime symbols missing\n";
        dlclose(handle);
        return 2;
    }
    uint64_t mac = parse_mac(mac_text);
    if (!mac) {
        std::cerr << "[bt] invalid MAC\n";
        dlclose(handle);
        return 2;
    }
    auto cert = read_file(cert_path);
    auto chip = read_file(chip_path);
    if (cert.empty() || chip.empty()) {
        std::cerr << "[bt] cert/chip file empty\n";
        dlclose(handle);
        return 2;
    }
    const char* certifier = std::getenv("ANYLOC_BT_CERTIFY_CMD");
    if (certifier) g_ctx.certify_cmd = certifier;
    void* object = create(mac);
    if (!object) {
        std::cerr << "[bt] CreateiOSBluetoothLocation failed\n";
        dlclose(handle);
        return 3;
    }
    g_ctx.object = object;
    int open_rc = open_channel(object);
    if (!open_rc) {
        std::cerr << "[bt] RFCOMM channel did not open\n";
        release(&object);
        dlclose(handle);
        return 4;
    }
    int rc = connect_with_credentials(object, cert, chip);
    if (rc != 0) {
        std::cerr << "[bt] iAP2 authentication failed\n";
        release(&object);
        dlclose(handle);
        return 5;
    }
    std::cout << "{\"ok\":true,\"type\":\"ready\"}\n" << std::flush;
    using SetLocationFn = int (*)(void*, float, float);
    auto set_location = reinterpret_cast<SetLocationFn>(get_slot(object, 5));
    std::string line;
    while (std::getline(std::cin, line)) {
        if (line == "quit" || line == "clear") break;
        double lat = 0, lon = 0;
        if (std::sscanf(line.c_str(), "%lf %lf", &lat, &lon) == 2) {
            int sr = set_location(object, static_cast<float>(lat), static_cast<float>(lon));
            std::cout << "{\"ok\":true,\"set_location_rc\":" << sr << "}\n" << std::flush;
        }
    }
    release(&object);
    dlclose(handle);
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 3) {
        std::cerr << "usage:\n"
                  << "  anyloc-bt-bridge probe <mac>\n"
                  << "  anyloc-bt-bridge connect <mac> <cert> <chip>\n";
        return 2;
    }
    std::string command = argv[1];
    if (command == "probe") return run_probe(argv[2]);
    if (command == "connect" && argc >= 5) return run_connect(argv[2], argv[3], argv[4]);
    std::cerr << "unknown command\n";
    return 2;
}
