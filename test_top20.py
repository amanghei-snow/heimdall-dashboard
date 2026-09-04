#!/usr/bin/env python3
"""Quick test: collect top 20 instances with concurrency=50, single-flag mode."""
import asyncio
import time
import config
from collector import _collect_instance, _save_cache

INSTANCES = [
    "novartiscorp", "atyourserviceportal", "aztech", "servicesdesjardins",
    "csc", "isgs", "unilever", "sanofiservices", "surf", "hsbcitid",
    "elevancehealth", "shell2", "citigroupitsm", "scbnow01",
    "servicecafe", "kroton", "itsmnow", "accentureinternal",
    "walmartglobal", "roche",
]


async def main():
    sem = asyncio.Semaphore(50)
    results = {}
    done = 0

    async def worker(inst):
        nonlocal done
        async with sem:
            data = await _collect_instance(inst)
        _save_cache(inst, data, bindings=config.RUCKUS_COMBINED_BINDINGS)
        results[inst] = data
        done += 1
        views = len(data.get("views", {}))
        errs = len(data.get("errors", []))
        stats = data.get("chunk_stats", {})
        times = [v.get("elapsed", 0) for v in stats.values()]
        avg_t = sum(times) / len(times) if times else 0
        max_t = max(times) if times else 0
        print(f"[{done}/20] {inst:30s} {views:>2} views  {errs:>3} errs  avg={avg_t:.1f}s max={max_t:.1f}s")

    t0 = time.time()
    tasks = [worker(i) for i in INSTANCES]
    await asyncio.gather(*tasks)
    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"Avg per instance: {elapsed/len(results):.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
