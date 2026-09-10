"""DeepSeek credentials: explicit environment override, then the macOS Keychain.

No credential is put in a command argument, configuration file, or database.
SecItem targets the login (file) keychain because this is an unsigned CLI, without
the provisioning profile required for the data-protection keychain.
"""
from contextlib import contextmanager
import ctypes as C
import os
import sys


class CredentialUnavailableError(Exception):
    """A safe error which never includes a Keychain value or native error body."""


class MacKeychain:
    SERVICE = "com.contextox.deepseek"
    ACCOUNT = "api-key"

    def __init__(self):
        if sys.platform != "darwin":
            raise CredentialUnavailableError()
        self.cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.sec = C.CDLL("/System/Library/Frameworks/Security.framework/Security")
        signatures = {
            "CFStringCreateWithCString": ([C.c_void_p, C.c_char_p, C.c_uint32], C.c_void_p),
            "CFDataCreate": ([C.c_void_p, C.c_char_p, C.c_long], C.c_void_p),
            "CFDictionaryCreateMutable": ([C.c_void_p, C.c_long, C.c_void_p, C.c_void_p], C.c_void_p),
            "CFDictionarySetValue": ([C.c_void_p, C.c_void_p, C.c_void_p], None),
            "CFDataGetLength": ([C.c_void_p], C.c_long),
            "CFDataGetBytePtr": ([C.c_void_p], C.c_void_p),
            "CFRelease": ([C.c_void_p], None),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.cf, name)
            function.argtypes, function.restype = args, result
        for name in ("SecItemAdd", "SecItemCopyMatching", "SecItemUpdate"):
            function = getattr(self.sec, name)
            function.argtypes = [C.c_void_p, C.c_void_p]
            function.restype = C.c_int32
        self.sec.SecItemDelete.argtypes = [C.c_void_p]
        self.sec.SecItemDelete.restype = C.c_int32

    def constant(self, name):
        return C.c_void_p.in_dll(self.cf if name.startswith("kCF") else self.sec, name).value

    @contextmanager
    def attributes(self, values):
        key_callbacks = C.addressof(C.c_byte.in_dll(self.cf, "kCFTypeDictionaryKeyCallBacks"))
        value_callbacks = C.addressof(C.c_byte.in_dll(self.cf, "kCFTypeDictionaryValueCallBacks"))
        dictionary = self.cf.CFDictionaryCreateMutable(None, 0, key_callbacks, value_callbacks)
        allocated = [dictionary]
        try:
            for name, value in values.items():
                if isinstance(value, bytes):
                    reference = self.cf.CFDataCreate(None, value, len(value))
                    allocated.append(reference)
                elif isinstance(value, str):
                    reference = self.cf.CFStringCreateWithCString(None, value.encode("utf-8"), 0x08000100)
                    allocated.append(reference)
                else:
                    reference = value
                if not reference:
                    raise CredentialUnavailableError()
                self.cf.CFDictionarySetValue(dictionary, self.constant(name), reference)
            yield dictionary
        finally:
            for reference in reversed(allocated):
                if reference:
                    self.cf.CFRelease(reference)

    def query(self):
        return {"kSecClass": self.constant("kSecClassGenericPassword"),
                "kSecAttrService": self.SERVICE, "kSecAttrAccount": self.ACCOUNT}

    def _find(self, *, secret):
        values = self.query() | {
            "kSecReturnData" if secret else "kSecReturnAttributes": self.constant("kCFBooleanTrue"),
            "kSecMatchLimit": self.constant("kSecMatchLimitOne"),
            "kSecUseAuthenticationUI": self.constant("kSecUseAuthenticationUIFail"),
        }
        result = C.c_void_p()
        with self.attributes(values) as query:
            status = self.sec.SecItemCopyMatching(query, C.byref(result))
        try:
            if status == -25300:  # errSecItemNotFound
                return None if secret else False
            if status != 0 or not result.value:
                raise CredentialUnavailableError()
            if not secret:
                return True
            length = self.cf.CFDataGetLength(result)
            if not 1 <= length <= 512:
                raise CredentialUnavailableError()
            return C.string_at(self.cf.CFDataGetBytePtr(result), length).decode("ascii")
        except (UnicodeError, ValueError):
            raise CredentialUnavailableError() from None
        finally:
            if result.value:
                self.cf.CFRelease(result)

    def contains(self):
        return self._find(secret=False)

    def read(self):
        return self._find(secret=True)

    def save(self, value: str):
        with self.attributes(self.query()) as query, self.attributes({"kSecValueData": value.encode("ascii")}) as update:
            status = self.sec.SecItemUpdate(query, update)
        if status == -25300:
            with self.attributes(self.query() | {"kSecValueData": value.encode("ascii"),
                    "kSecAttrLabel": "数契 · DeepSeek API Key"}) as item:
                status = self.sec.SecItemAdd(item, None)
        if status != 0:
            raise CredentialUnavailableError()

    def remove(self):
        with self.attributes(self.query()) as query:
            status = self.sec.SecItemDelete(query)
        if status not in (0, -25300):
            raise CredentialUnavailableError()


def environment_key() -> str | None:
    return os.environ.get("DEEPSEEK_API_KEY") or None


def provider_key() -> str | None:
    return environment_key() or MacKeychain().read()
