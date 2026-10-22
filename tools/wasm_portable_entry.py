"""Frozen Windows pilot entry, including the private Wasmtime subprocess."""
import sys


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--https-probe":
        import traceback
        import urllib.error
        import urllib.request
        try:
            with urllib.request.urlopen("https://api.deepseek.com", timeout=15) as response:
                print("HTTPS:", response.status)
        except urllib.error.HTTPError as exc:
            print("HTTPS connected; HTTP status:", exc.code)
        except Exception:
            traceback.print_exc()
            raise SystemExit(1)
        raise SystemExit(0)
    if len(sys.argv) == 3 and sys.argv[1] == "--wasm-worker":
        from cpp_assessor.sandbox.wasm_worker import guarded_main
        raise SystemExit(guarded_main(sys.argv[2]))
    from cpp_assessor.interfaces.cli import main
    if len(sys.argv) == 1:
        sys.argv.extend(["web", "--backend", "wasm_pilot", "--allow-unattested-backend", "--port", "8766"])
    raise SystemExit(main())
