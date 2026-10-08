"""Generate a fixed synthetic courseware sample for SlideNote benchmarks.

The deck deterministically covers the P0 sample-set feature matrix from the
roadmap: text-dense pages, tables, formulas, chart-like figures, scanned
(image-only) pages and a long page count. Run:

    python scripts/make_sample_deck.py --out benchmarks/samples/synthetic_course.pdf

The output is byte-stable across runs on the same PyMuPDF version, so it can
serve as a fixed benchmark input in CI and in real-courseware evaluations.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pymupdf

COURSE_TITLE = "操作系统导出：进程与内存（基准样例课件）"

TEXT_DENSE_SECTIONS = [
    (
        "进程的概念",
        [
            "进程是一个正在执行的程序实例，它不仅包含程序的代码，还包含程序计数器、寄存器内容和进程地址空间等执行状态。",
            "操作系统通过进程控制块（PCB）记录每个进程的标识、状态、优先级、调度信息和资源清单；PCB 是操作系统感知进程存在的唯一标志。",
            "进程的三种基本状态是就绪、运行和阻塞：就绪进程等待调度器分配 CPU，运行进程正在占用 CPU，阻塞进程等待某个事件完成后回到就绪队列。",
            "状态转换由特定事件驱动：运行到就绪通常因为时间片耗尽，运行到阻塞通常因为请求 I/O 或等待锁，阻塞到就绪则由事件完成触发。",
        ],
    ),
    (
        "线程与并发",
        [
            "线程是进程内的一条执行路径，同一进程内的线程共享地址空间、打开文件表和信号处理器，但各自拥有独立的栈和寄存器上下文。",
            "引入线程的收益来自两个方向：一是切换成本低于进程切换，因为无需更换地址空间；二是同进程线程间的数据共享不需要额外的通信机制。",
            "用户级线程由线程库管理，内核不感知它们的存在；内核级线程由内核直接调度，能够利用多核并行，但创建和切换需要陷入内核。",
            "并发与并行的区别在于时间维度：并发指多个执行流在同一时间间隔内交替推进，并行指多个执行流在同一时刻同时推进，需要多核硬件支持。",
        ],
    ),
    (
        "虚拟内存",
        [
            "虚拟内存为每个进程提供独立的、可比物理内存更大的地址空间，其核心机制是把地址空间切分成固定大小的页，并按需调入物理内存。",
            "页表记录虚拟页号到物理页框号的映射，并维护有效位、修改位和访问位；有效位为 0 的页访问会触发缺页异常，由内核负责调入。",
            "缺页处理的代价主要来自磁盘 I/O，因此替换策略直接影响性能：LRU 用最近的访问历史近似未来访问，时钟算法用访问位做出低开销的近似 LRU。",
            "工作集模型指出，进程在一段时间内稳定访问的页面集合称为工作集；只要物理内存能容纳所有活跃进程的工作集，抖动就可以避免。",
        ],
    ),
    (
        "文件系统",
        [
            "文件系统把存储设备组织成文件和目录，并通过元数据描述文件的所有者、权限、大小和数据块位置。",
            "索引节点（inode）是大多数类 Unix 文件系统的核心结构，它保存文件元数据和数据块指针，目录项则把文件名映射到 inode 编号。",
            "日志式文件系统在写入数据前先把操作记录写入日志，崩溃后可以重放日志恢复一致性，避免文件系统长时间自检。",
            "磁盘调度需要权衡吞吐量与公平性：SSTF 总是选择最近的请求以减少寻道时间，但可能让边缘请求长期等待。",
        ],
    ),
]

TABLE_PAGE = {
    "title": "调度算法对比",
    "headers": ["算法", "选择依据", "优点", "风险"],
    "rows": [
        ["FCFS", "到达顺序", "实现简单、无饥饿", "平均等待时间长"],
        ["SJF", "最短作业优先", "平均等待时间最优", "长作业可能饥饿"],
        ["RR", "时间片轮转", "响应快、公平", "时间片选择敏感"],
        ["MLFQ", "多级队列", "兼顾交互与吞吐", "参数调优复杂"],
    ],
}

FORMULA_PAGE = {
    "title": "关键公式",
    "lines": [
        "有效访问时间 = α × (缺页处理时间) + (1 − α) × (内存访问时间)",
        "页错误率上限：EAT ≤ 基准访问时间 × (1 + p × t)",
        "时钟算法命中率 ≈ 1 − (1 − h)^k",
        "工作集窗口：WS(t, Δ) = { 页 x | 最近 Δ 次引用中出现过 x }",
    ],
}

CHART_PAGE = {
    "title": "多级队列调度下的吞吐量",
    "series": [12, 18, 24, 27, 26, 22, 17],
}

SCANNED_PAGES = [
    (
        "扫描页：课堂笔记",
        [
            "课堂记录：就绪队列过长时，交互进程的响应时间会明显变差。",
            "课堂记录：时间片并不是越大越好，过大的时间片会退化为 FCFS。",
            "课堂记录：缺页率与工作集大小呈非线性关系。",
        ],
    ),
]

TAIL_SECTIONS = [
    (
        "进程通信",
        [
            "进程间通信的两种基本模型是共享内存和消息传递：共享内存快但需要用户自己处理同步，消息传递慢但由内核保证互斥。",
            "管道是最常见的消息传递形式，匿名管道用于父子进程，命名管道允许无亲缘关系的进程交换数据。",
        ],
    ),
    (
        "死锁",
        [
            "死锁的四个必要条件是互斥、持有并等待、不可抢占和循环等待；破坏任意一个条件即可预防死锁。",
            "银行家算法通过安全性检查判断一次资源分配是否可能进入不安全状态，从而避免死锁。",
        ],
    ),
    (
        "同步机制",
        [
            "信号量用原子 P/V 操作控制资源计数，互斥锁保证临界区互斥进入，条件变量用于等待复杂条件成立。",
            "管程把共享数据和同步操作封装在一个模块内，编译器保证同一时刻只有一个线程活跃在管程内。",
        ],
    ),
    (
        "I/O 控制",
        [
            "程序直接控制占用 CPU 轮询状态，中断驱动让 CPU 在设备就绪时才介入，DMA 让整块数据的传送脱离 CPU。",
            "设备驱动向上屏蔽硬件差异，把统一的读写请求翻译成设备寄存器操作。",
        ],
    ),
]


def _cjk_text(page: pymupdf.Page, y: float, text: str, size: float = 11, width: float = 500) -> float:
    """Insert wrapped CJK text, return the y position after the block."""
    font = "china-s"
    x, line_height = 60.0, size * 1.75
    line = ""
    for char in text:
        if y > 760:
            break
        if pymupdf.get_text_length(char, fontname=font, fontsize=size) + pymupdf.get_text_length(
            line, fontname=font, fontsize=size
        ) > width:
            page.insert_text((x, y), line, fontname=font, fontsize=size)
            y += line_height
            line = char
        else:
            line += char
    if line:
        page.insert_text((x, y), line, fontname=font, fontsize=size)
        y += line_height
    return y


def _page_title(page: pymupdf.Page, title: str) -> float:
    page.insert_text((60, 80), title, fontname="china-s", fontsize=17)
    return 120.0


def _text_dense_page(doc: pymupdf.Page | None, doc_handle, title: str, paragraphs: list[str], page_no: int) -> None:
    page = doc_handle.new_page()
    page.insert_text((40, 40), f"{page_no}", fontname="helv", fontsize=9)
    y = _page_title(page, title)
    for paragraph in paragraphs:
        y = _cjk_text(page, y, paragraph)
        y += 10


def build_sample_pdf(path: Path) -> Path:
    doc = pymupdf.open()

    # 1: front matter (title page)
    page = doc.new_page()
    page.insert_text((60, 200), COURSE_TITLE, fontname="china-s", fontsize=22)
    page.insert_text((60, 250), "基准样例：固定样本集 / 用于可复现评测", fontname="china-s", fontsize=12)

    # 2-5: text-dense sections
    page_no = 2
    for title, paragraphs in TEXT_DENSE_SECTIONS:
        _text_dense_page(None, doc, title, paragraphs, page_no)
        page_no += 1

    # 6: table page
    page = doc.new_page()
    page.insert_text((40, 40), f"{page_no}", fontname="helv", fontsize=9)
    y = _page_title(page, TABLE_PAGE["title"])
    col_x = [60, 170, 330, 460]
    for index, header in enumerate(TABLE_PAGE["headers"]):
        page.insert_text((col_x[index], y), header, fontname="china-s", fontsize=11)
    y += 24
    for row in TABLE_PAGE["rows"]:
        for index, cell in enumerate(row):
            page.insert_text((col_x[index], y), cell, fontname="china-s", fontsize=10)
        y += 22
    page_no += 1

    # 7: formula page
    page = doc.new_page()
    page.insert_text((40, 40), f"{page_no}", fontname="helv", fontsize=9)
    y = _page_title(page, FORMULA_PAGE["title"])
    for line in FORMULA_PAGE["lines"]:
        y = _cjk_text(page, y, line, size=13)
    page_no += 1

    # 8: chart page (vector line chart)
    page = doc.new_page()
    page.insert_text((40, 40), f"{page_no}", fontname="helv", fontsize=9)
    y = _page_title(page, CHART_PAGE["title"])
    origin = pymupdf.Point(80, 600)
    page.draw_line(origin, origin + pymupdf.Point(440, 0))
    page.draw_line(origin, origin + pymupdf.Point(0, -320))
    step = 440 / (len(CHART_PAGE["series"]) - 1)
    points = [
        pymupdf.Point(origin.x + index * step, origin.y - value * 11)
        for index, value in enumerate(CHART_PAGE["series"])
    ]
    page.draw_polyline(points, color=(0.1, 0.3, 0.7), width=1.6)
    for index, point in enumerate(points):
        page.draw_circle(point, 2.5, color=(0.7, 0.2, 0.1), fill=(0.7, 0.2, 0.1))
        page.insert_text((point.x - 6, origin.y + 18), f"T{index + 1}", fontname="helv", fontsize=8)
    page.insert_text((origin.x, origin.y + 40), "观测窗口（单位时间）", fontname="china-s", fontsize=10)
    page_no += 1

    # 9-10: scanned pages: rasterize a rendered text page and embed as image only
    for title, lines in SCANNED_PAGES:
        temp = doc.new_page()
        y = _page_title(temp, title)
        for line in lines:
            y = _cjk_text(temp, y, line)
        pix = temp.get_pixmap(dpi=72)
        temp_number = temp.number
        scanned = doc.new_page()
        scanned.insert_image(scanned.rect, pixmap=pix)
        doc.delete_page(temp_number)
        page_no += 1

    # 11-20: tail sections + repeated exercise pages to make the deck long
    for section in TAIL_SECTIONS:
        _text_dense_page(None, doc, section[0], section[1], page_no)
        page_no += 1
    for index in range(1, 6):
        _text_dense_page(
            None,
            doc,
            f"练习与思考 {index}",
            [
                f"第 {index} 组练习：给定一组到达时间和估计运行时间，分别计算 FCFS、SJF 和 RR（时间片 = 2）的平均周转时间，并比较结论是否随时间片变化。",
                f"第 {index} 组练习：假设某进程的页访问序列已知，用 LRU 和时钟算法分别模拟缺页次数，解释两种策略产生差异的页面是哪些。",
            ],
            page_no,
        )
        page_no += 1

    doc.save(path, deflate=True)
    doc.close()
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("benchmarks/samples/synthetic_course.pdf"))
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    build_sample_pdf(args.out)
    doc = pymupdf.open(args.out)
    print(f"sample deck written: {args.out} ({len(doc)} pages)")
    doc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
