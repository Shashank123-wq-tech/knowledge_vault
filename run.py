import argparse
import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run("app.main:create_app", factory=True, host=args.host, port=args.port, reload=args.reload)
