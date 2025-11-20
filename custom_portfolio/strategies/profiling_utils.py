import os

import yappi


def run_with_yappi(label: str, func):
    """Run the provided callable under Yappi profiling and persist stats."""
    yappi.set_clock_type("cpu")
    yappi.start()
    try:
        return func()
    finally:
        yappi.stop()
        stats = yappi.get_func_stats()
        stats.sort("ttot", "desc")
        pstat_path = os.path.abspath(f"yappi_{label}.pstat")
        stats.save(pstat_path, type="pstat")
        txt_path = os.path.abspath(f"yappi_{label}.txt")
        with open(txt_path, "w") as f:
            stats.print_all(out=f)
        thread_stats = yappi.get_thread_stats()
        thread_txt = os.path.abspath(f"yappi_{label}_threads.txt")
        with open(thread_txt, "w") as f:
            thread_stats.print_all(out=f)
        yappi.clear_stats()
