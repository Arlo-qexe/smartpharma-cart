// Mínimo de <Arduino.h> para compilar en el PC lo que usa shared/protocol_constants.h (String, uint8_t, snprintf). Solo banco de pruebas (-I test_host).
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string>
class String {
public:
    std::string s;
    String() {}
    String(const char *c) : s(c ? c : "") {}
    String(const std::string &c) : s(c) {}
    size_t length() const { return s.size(); }
    char operator[](size_t i) const { return s[i]; }
    const char *c_str() const { return s.c_str(); }
    String operator+(const String &o) const { return String(s + o.s); }
    String operator+(char c) const { return String(s + c); }
};
