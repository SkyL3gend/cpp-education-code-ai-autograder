// 极简 JSON 解析与序列化（只供测评驱动使用，无第三方依赖）
//
// 支持的子集：对象、数组、字符串（含 \uXXXX 与代理对）、数字、true/false/null。
// 不支持的扩展：注释、NaN/Infinity、尾随逗号、单引号。
#pragma once

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

namespace jsonlite {

class ParseError : public std::runtime_error {
public:
    explicit ParseError(const std::string& message) : std::runtime_error(message) {}
};

struct Value {
    enum Type { NUL, BOOL, NUM, STR, ARR, OBJ };

    Type type = NUL;
    bool boolValue = false;
    double numValue = 0.0;
    std::string strValue;
    std::vector<Value> arrValue;
    std::map<std::string, Value> objValue;

    Value() {}

    static Value object() {
        Value v;
        v.type = OBJ;
        return v;
    }
    static Value array() {
        Value v;
        v.type = ARR;
        return v;
    }
    static Value of(bool b) {
        Value v;
        v.type = BOOL;
        v.boolValue = b;
        return v;
    }
    static Value of(int n) {
        Value v;
        v.type = NUM;
        v.numValue = static_cast<double>(n);
        return v;
    }
    static Value of(double n) {
        Value v;
        v.type = NUM;
        v.numValue = n;
        return v;
    }
    static Value of(const std::string& s) {
        Value v;
        v.type = STR;
        v.strValue = s;
        return v;
    }

    bool isObject() const { return type == OBJ; }
    bool isArray() const { return type == ARR; }
    bool isString() const { return type == STR; }
    bool isNumber() const { return type == NUM; }
    bool isBool() const { return type == BOOL; }

    Value& operator[](const std::string& key) {
        type = OBJ;
        return objValue[key];
    }

    void push(const Value& item) {
        type = ARR;
        arrValue.push_back(item);
    }

    const Value* find(const std::string& key) const {
        if (type != OBJ) {
            return nullptr;
        }
        std::map<std::string, Value>::const_iterator it = objValue.find(key);
        return (it == objValue.end()) ? nullptr : &it->second;
    }

    std::string stringOr(const std::string& key, const std::string& fallback) const {
        const Value* v = find(key);
        if (v == nullptr || !v->isString()) {
            return fallback;
        }
        return v->strValue;
    }

    int intOr(const std::string& key, int fallback) const {
        const Value* v = find(key);
        if (v == nullptr || !v->isNumber()) {
            return fallback;
        }
        return static_cast<int>(v->numValue);
    }
};

namespace detail {

inline void appendUtf8(std::string& out, unsigned int codePoint) {
    if (codePoint < 0x80u) {
        out.push_back(static_cast<char>(codePoint));
    } else if (codePoint < 0x800u) {
        out.push_back(static_cast<char>(0xC0u | (codePoint >> 6)));
        out.push_back(static_cast<char>(0x80u | (codePoint & 0x3Fu)));
    } else if (codePoint < 0x10000u) {
        out.push_back(static_cast<char>(0xE0u | (codePoint >> 12)));
        out.push_back(static_cast<char>(0x80u | ((codePoint >> 6) & 0x3Fu)));
        out.push_back(static_cast<char>(0x80u | (codePoint & 0x3Fu)));
    } else {
        out.push_back(static_cast<char>(0xF0u | (codePoint >> 18)));
        out.push_back(static_cast<char>(0x80u | ((codePoint >> 12) & 0x3Fu)));
        out.push_back(static_cast<char>(0x80u | ((codePoint >> 6) & 0x3Fu)));
        out.push_back(static_cast<char>(0x80u | (codePoint & 0x3Fu)));
    }
}

inline int hexValue(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

class Parser {
public:
    explicit Parser(const std::string& text) : text_(text), pos_(0), depth_(0) {}

    Value parseDocument() {
        skipSpace();
        Value value = parseValue();
        skipSpace();
        if (pos_ != text_.size()) {
            throw ParseError("trailing characters after top-level value");
        }
        return value;
    }

private:
    const std::string& text_;
    std::size_t pos_;
    int depth_;

    void skipSpace() {
        while (pos_ < text_.size()) {
            char c = text_[pos_];
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
                ++pos_;
            } else {
                break;
            }
        }
    }

    char peek() {
        if (pos_ >= text_.size()) {
            throw ParseError("unexpected end of input");
        }
        return text_[pos_];
    }

    void expect(char c) {
        if (pos_ >= text_.size() || text_[pos_] != c) {
            throw ParseError(std::string("expected '") + c + "'");
        }
        ++pos_;
    }

    bool consumeLiteral(const char* literal) {
        std::size_t n = std::char_traits<char>::length(literal);
        if (text_.compare(pos_, n, literal) != 0) {
            return false;
        }
        pos_ += n;
        return true;
    }

    Value parseValue() {
        if (pos_ >= text_.size()) {
            throw ParseError("unexpected end of input");
        }
        char c = text_[pos_];
        if (c == '{') return parseObject();
        if (c == '[') return parseArray();
        if (c == '"') return Value::of(parseString());
        if (c == 't') {
            if (!consumeLiteral("true")) throw ParseError("invalid literal");
            return Value::of(true);
        }
        if (c == 'f') {
            if (!consumeLiteral("false")) throw ParseError("invalid literal");
            return Value::of(false);
        }
        if (c == 'n') {
            if (!consumeLiteral("null")) throw ParseError("invalid literal");
            return Value();
        }
        if (c == '-' || (c >= '0' && c <= '9')) return parseNumber();
        throw ParseError("unexpected character in value");
    }

    Value parseObject() {
        if (++depth_ > 128) throw ParseError("nesting too deep");
        expect('{');
        Value result = Value::object();
        skipSpace();
        if (pos_ < text_.size() && text_[pos_] == '}') {
            ++pos_;
            --depth_;
            return result;
        }
        while (true) {
            skipSpace();
            std::string key = parseString();
            skipSpace();
            expect(':');
            skipSpace();
            result[key] = parseValue();
            skipSpace();
            if (pos_ < text_.size() && text_[pos_] == ',') {
                ++pos_;
                continue;
            }
            expect('}');
            break;
        }
        --depth_;
        return result;
    }

    Value parseArray() {
        if (++depth_ > 128) throw ParseError("nesting too deep");
        expect('[');
        Value result = Value::array();
        skipSpace();
        if (pos_ < text_.size() && text_[pos_] == ']') {
            ++pos_;
            --depth_;
            return result;
        }
        while (true) {
            skipSpace();
            result.push(parseValue());
            skipSpace();
            if (pos_ < text_.size() && text_[pos_] == ',') {
                ++pos_;
                continue;
            }
            expect(']');
            break;
        }
        --depth_;
        return result;
    }

    std::string parseString() {
        expect('"');
        std::string out;
        while (true) {
            if (pos_ >= text_.size()) {
                throw ParseError("unterminated string");
            }
            unsigned char c = static_cast<unsigned char>(text_[pos_]);
            if (c == '"') {
                ++pos_;
                break;
            }
            if (c == '\\') {
                ++pos_;
                if (pos_ >= text_.size()) throw ParseError("unterminated escape");
                char e = text_[pos_++];
                switch (e) {
                    case '"': out.push_back('"'); break;
                    case '\\': out.push_back('\\'); break;
                    case '/': out.push_back('/'); break;
                    case 'b': out.push_back('\b'); break;
                    case 'f': out.push_back('\f'); break;
                    case 'n': out.push_back('\n'); break;
                    case 'r': out.push_back('\r'); break;
                    case 't': out.push_back('\t'); break;
                    case 'u': {
                        unsigned int cp = parseHex4();
                        if (cp >= 0xD800u && cp <= 0xDBFFu) {
                            if (pos_ + 1 < text_.size() && text_[pos_] == '\\' && text_[pos_ + 1] == 'u') {
                                pos_ += 2;
                                unsigned int low = parseHex4();
                                if (low >= 0xDC00u && low <= 0xDFFFu) {
                                    cp = 0x10000u + ((cp - 0xD800u) << 10) + (low - 0xDC00u);
                                } else {
                                    throw ParseError("invalid surrogate pair");
                                }
                            } else {
                                throw ParseError("lone high surrogate");
                            }
                        }
                        appendUtf8(out, cp);
                        break;
                    }
                    default:
                        throw ParseError("invalid escape character");
                }
                continue;
            }
            if (c < 0x20u) {
                throw ParseError("control character in string");
            }
            out.push_back(static_cast<char>(c));
            ++pos_;
        }
        return out;
    }

    unsigned int parseHex4() {
        unsigned int value = 0;
        for (int i = 0; i < 4; ++i) {
            if (pos_ >= text_.size()) throw ParseError("truncated \\u escape");
            int h = hexValue(text_[pos_++]);
            if (h < 0) throw ParseError("invalid hex digit");
            value = value * 16u + static_cast<unsigned int>(h);
        }
        return value;
    }

    Value parseNumber() {
        const char* begin = text_.c_str() + pos_;
        char* end = nullptr;
        double number = std::strtod(begin, &end);
        if (end == begin) {
            throw ParseError("invalid number");
        }
        pos_ = static_cast<std::size_t>(end - text_.c_str());
        return Value::of(number);
    }
};

inline void writeString(const std::string& s, std::string& out) {
    out.push_back('"');
    for (std::size_t i = 0; i < s.size(); ++i) {
        unsigned char c = static_cast<unsigned char>(s[i]);
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20u) {
                    char buffer[8];
                    std::snprintf(buffer, sizeof(buffer), "\\u%04x", static_cast<unsigned int>(c));
                    out += buffer;
                } else {
                    out.push_back(static_cast<char>(c));
                }
                break;
        }
    }
    out.push_back('"');
}

inline void writeValue(const Value& value, std::string& out) {
    switch (value.type) {
        case Value::NUL:
            out += "null";
            break;
        case Value::BOOL:
            out += (value.boolValue ? "true" : "false");
            break;
        case Value::NUM: {
            char buffer[40];
            double d = value.numValue;
            if (std::fabs(d) < 1e15 && d == static_cast<double>(static_cast<long long>(d))) {
                std::snprintf(buffer, sizeof(buffer), "%lld", static_cast<long long>(d));
            } else {
                std::snprintf(buffer, sizeof(buffer), "%.17g", d);
            }
            out += buffer;
            break;
        }
        case Value::STR:
            writeString(value.strValue, out);
            break;
        case Value::ARR: {
            out.push_back('[');
            for (std::size_t i = 0; i < value.arrValue.size(); ++i) {
                if (i != 0) out.push_back(',');
                writeValue(value.arrValue[i], out);
            }
            out.push_back(']');
            break;
        }
        case Value::OBJ: {
            out.push_back('{');
            bool first = true;
            for (std::map<std::string, Value>::const_iterator it = value.objValue.begin();
                 it != value.objValue.end(); ++it) {
                if (!first) out.push_back(',');
                first = false;
                writeString(it->first, out);
                out.push_back(':');
                writeValue(it->second, out);
            }
            out.push_back('}');
            break;
        }
    }
}

}  // namespace detail

inline Value parse(const std::string& text) {
    detail::Parser parser(text);
    return parser.parseDocument();
}

inline std::string dump(const Value& value) {
    std::string out;
    detail::writeValue(value, out);
    return out;
}

}  // namespace jsonlite
