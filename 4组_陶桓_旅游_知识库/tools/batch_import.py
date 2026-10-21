"""批量导入旅游文档：默认调用 8004 /upload，也可用 --local 直接跑入库图。

用法：
    uv run python tools/batch_import.py                      # 走 8004 服务
    uv run python tools/batch_import.py --local              # 不进服务，直接跑图（需 GPU/LLM）
    uv run python tools/batch_import.py --local --only 线路   # 只导入文件名含「线路」的
    uv run python tools/batch_import.py --dry-run            # 只列出将导入的文件

前置：使用 --local 时会直接写 Milvus，请确认 .env 指向新的旅游集合。
"""
import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = PROJECT_ROOT / "旅游数据"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def upload_one(base_url: str, path: Path) -> str:
    import requests

    with path.open("rb") as fh:
        resp = requests.post(
            f"{base_url}/upload",
            files={"file": (path.name, fh, "text/markdown")},
            timeout=60,
        )
    resp.raise_for_status()
    return resp.json()["task_id"]


def wait_done(base_url: str, task_id: str, timeout: int, interval: float = 2.0) -> tuple[str, dict]:
    import requests

    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        try:
            resp = requests.get(f"{base_url}/status/{task_id}", timeout=30)
            resp.raise_for_status()
            last = resp.json()
        except requests.RequestException as exc:
            last = {"status": "request_error", "error": str(exc)}
        status = str(last.get("status") or "")
        if status in ("completed", "failed"):
            return status, last
        time.sleep(interval)
    return "timeout", last


def import_local(path: Path, index: int) -> tuple[str, dict]:
    """不进服务，直接跑入库图；先把文件复制到临时目录，避免 chunks.json 落到数据目录。"""
    import shutil
    import tempfile

    from atguigu.import_process.import_main_graph import MainGraphRunner

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        md = tmp_dir / path.name
        shutil.copyfile(path, md)
        state = {
            "task_id": f"batch-{index}",
            "local_dir": str(tmp_dir),
            "local_file_path": str(md),
        }
        result = MainGraphRunner.create_and_run(state) or {}
        meta = result.get("doc_metadata") or {}
        return "completed", {
            "content_type": meta.get("content_type"),
            "city": meta.get("city"),
            "chunks": len(result.get("chunks") or []),
            "warnings": meta.get("warnings") or [],
        }
    except Exception as exc:  # noqa: BLE001
        return "failed", {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="批量导入旅游文档到知识库")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="待导入目录")
    parser.add_argument("--base-url", default="http://127.0.0.1:8004", help="导入服务地址")
    parser.add_argument("--timeout", type=int, default=900, help="单份文档等待上限（秒）")
    parser.add_argument("--only", default="", help="只导入文件名包含该关键字的文档")
    parser.add_argument("--dry-run", action="store_true", help="只列出文件，不上传")
    parser.add_argument(
        "--local",
        action="store_true",
        help="不调用 8004，直接在本进程跑入库图（需 GPU 与 LLM 可用）",
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"目录不存在：{root}")
        return 2

    files = sorted(root.rglob("*.md"))
    if args.only:
        files = [p for p in files if args.only in p.name]
    if not files:
        print("没有匹配的文档")
        return 2

    print(f"待导入 {len(files)} 份，服务地址 {args.base_url}")
    if args.dry_run:
        for path in files:
            print(f"  - {path.relative_to(root)}")
        return 0

    results = []
    for index, path in enumerate(files, start=1):
        rel = path.relative_to(root)
        print(f"[{index}/{len(files)}] {rel} ... ", end="", flush=True)
        started = time.time()
        try:
            if args.local:
                status, info = import_local(path, index)
            else:
                task_id = upload_one(args.base_url, path)
                status, info = wait_done(args.base_url, task_id, args.timeout)
        except Exception as exc:  # noqa: BLE001
            status, info = "upload_error", {"error": f"{type(exc).__name__}: {exc}"}
        elapsed = round(time.time() - started, 1)
        print(f"{status} ({elapsed}s)")
        results.append({"file": str(rel), "status": status, "seconds": elapsed, "info": info})

    print("\n" + "=" * 70)
    ok = [r for r in results if r["status"] == "completed"]
    bad = [r for r in results if r["status"] != "completed"]
    print(f"成功 {len(ok)} / {len(results)}")
    if bad:
        print("\n失败明细：")
        for item in bad:
            info = item["info"] or {}
            detail = info.get("error") or info.get("done_list") or info
            print(f"  - {item['file']} | {item['status']} | {detail}")

    # 按目录统计，便于核对 6 类是否都有数据
    by_dir = {}
    for item in ok:
        key = Path(item["file"]).parent.name
        by_dir[key] = by_dir.get(key, 0) + 1
    if by_dir:
        print("\n成功明细（按目录）：")
        for key, count in sorted(by_dir.items()):
            print(f"  {key}: {count} 份")

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
