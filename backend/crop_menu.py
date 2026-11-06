# -*- coding: utf-8 -*-
"""从 13 张实拍菜单页中自动检测并裁剪菜品照片。

思路：菜单纸背景（米色）占画面大部分，菜品照片是其中颜色/纹理与纸面
差异最大的矩形区域——
  1. 估计纸面颜色（亮度较高的中位数色，仅供调试参考）
  2. 颜色距离掩码 + 局部方差掩码 合成候选掩码
  3. 小核形态学闭合（MaxFilter→MinFilter）补洞，连通域提框
  4. 照片行/列组提取：框内按行找「连续照片像素行组」（仅饱和度信号，
     文字短笔画与黑墨插画天然排除），行组内再按列分组——厚文字带、
     插画、装订条等都能被剔除，并排照片也能拆开
  5. 多阈值搜索：选与「每页预期照片数」一致的阈值组合

产出：
  web/uploads/dishes/01.jpg …（菜品照片，供 dish.image 引用）
  web/uploads/menu_pages/01.jpg …（13 张原始菜单页备份）
  _crop_check.png（拼贴图，供人工核对，核对后可删）
"""
import os
import sys

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

SRC_DIR = r"C:\Users\Admin\Desktop\餐厅小程序\菜单"
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.normpath(os.path.join(BACKEND_DIR, "..", "web", "uploads"))
DISH_DIR = os.path.join(UPLOAD_DIR, "dishes")
PAGE_DIR = os.path.join(UPLOAD_DIR, "menu_pages")

# 每页：源文件名 → 预期照片（按 上→下 顺序的菜名）
PAGES = [
    ("微信图片_20261025132704_783_31.jpg", ["香酥鸡", "风葫芦"]),
    ("微信图片_20261025132715_784_31.jpg", ["蒜炝羊肚", "香辣肉丝", "烧羊血"]),
    ("微信图片_20261025132721_785_31.jpg", ["羊肉锅", "羊蝎子锅"]),
    ("微信图片_20261025132730_786_31.jpg", ["羊杂锅", "家常火锅"]),
    ("微信图片_20261025132735_787_31.jpg", ["锅仔毛血旺", "重庆辣子鸡"]),
    ("微信图片_20261025132744_788_31.jpg", ["五香牛肉"]),
    ("微信图片_20261025132748_789_31.jpg", ["孜然羊肉"]),
    ("微信图片_20261025132800_790_31.jpg", ["回锅肉"]),
    ("微信图片_20261025132809_791_31.jpg", ["豆角烧茄子"]),
    ("微信图片_20261025132813_792_31.jpg", ["炝锅鱼"]),
    ("微信图片_20261025132819_793_31.jpg", ["西红柿疙瘩汤"]),
    ("微信图片_20261025132824_794_31.jpg", ["羊肉胡卜", "羊肉泡馍", "羊杂汤"]),
    ("微信图片_20261025132828_795_31.jpg", ["羊肉饺子"]),
]


def box_mean(img, win):
    """滑动窗口均值（cumsum 实现，win 为窗口半径）。"""
    h, w = img.shape
    cs = np.zeros((h + 1, w + 1), dtype=np.float64)
    cs[1:, 1:] = np.cumsum(np.cumsum(img.astype(np.float64), axis=0), axis=1)
    r1 = np.clip(np.arange(h) - win, 0, h)
    r2 = np.clip(np.arange(h) + win + 1, 0, h)
    c1 = np.clip(np.arange(w) - win, 0, w)
    c2 = np.clip(np.arange(w) + win + 1, 0, w)
    top = cs[r1, :][:, c1]
    top_r = cs[r1, :][:, c2]
    bot = cs[r2, :][:, c1]
    bot_r = cs[r2, :][:, c2]
    area = (r2 - r1)[:, None] * (c2 - c1)[None, :]
    return (top + bot_r - top_r - bot) / np.maximum(area, 1)


def detect_components(mask):
    """4 连通域 BFS，返回 [(area, x0, y0, x1, y1)]。"""
    h, w = mask.shape
    visited = np.zeros((h, w), dtype=bool)
    comps = []
    for y in range(h):
        for x in range(w):
            if not mask[y, x] or visited[y, x]:
                continue
            stack = [(y, x)]
            visited[y, x] = True
            area = 0
            y0, y1, x0, x1 = y, y, x, x
            while stack:
                cy, cx = stack.pop()
                area += 1
                if cy < y0:
                    y0 = cy
                if cy > y1:
                    y1 = cy
                if cx < x0:
                    x0 = cx
                if cx > x1:
                    x1 = cx
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        stack.append((ny, nx))
            comps.append((area, x0, y0, x1, y1))
    return comps


def row_col_solid(mask, k):
    """按行/列计算「最长连续 True 长度」，>=k 视为照片行/列。

    照片内部是连续大色块（行长可达数百像素），菜单文字是稀疏短笔画
    （单字宽 < 60px），用连续长度可稳定区分。
    """
    h, w = mask.shape
    rows = np.zeros(h, dtype=bool)
    cols = np.zeros(w, dtype=bool)
    for y in range(h):
        row = mask[y]
        if not row.any():
            continue
        d = np.diff(np.concatenate(([False], row, [False])).astype(np.int8))
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1)
        rows[y] = starts.size > 0 and (ends - starts).max() >= k
    for x in range(w):
        col = mask[:, x]
        if not col.any():
            continue
        d = np.diff(np.concatenate(([False], col, [False])).astype(np.int8))
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1)
        cols[x] = starts.size > 0 and (ends - starts).max() >= k
    return rows, cols


def split_and_refine(boxes, base_p):
    """按照片像素组切分候选框并收缩掉文字边。

    base_p：照片像素掩码（仅饱和度信号）。对每个候选框：先按行找连续照片行组
    （容忍 gap 行小间隙），行组内再按列分组；并排的多个列组 = 多张照片分别输出。
    行判定要求存在 >=k_run 的连续照片像素：照片实心区长段成立，文字短笔画、
    黑墨插画（低饱和）与纸面（白/灰）天然排除——比旧「外缘收缩」更能去掉
    照片上下的文字列表与整页合并框里的非照片区域。
    """
    h, w = base_p.shape
    win = max(3, min(h, w) // 70)
    gap = max(3, min(h, w) // 100)
    k_run = max(int(0.07 * min(h, w)), 45)
    # 行标志：该行存在 >=k_run 的连续照片像素
    row_flag = np.zeros(h, dtype=bool)
    for y in range(h):
        row = base_p[y]
        if not row.any():
            continue
        d = np.diff(np.concatenate(([False], row, [False])).astype(np.int8))
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1)
        row_flag[y] = starts.size > 0 and (ends - starts).max() >= k_run

    def groups(flags, lo, hi):
        """True 位置按间隙 >gap 分组，返回 [a, b] 列表。"""
        out = []
        start = last = None
        for i in range(lo, hi + 1):
            if flags[i]:
                if start is None:
                    start = i
                last = i
            elif start is not None and i - last > gap:
                out.append([start, last])
                start = last = None
        if start is not None:
            out.append([start, last])
        return out

    out = []
    for bx in boxes:
        x0, y0, x1, y1 = bx
        x0 = max(0, x0)
        x1 = min(w - 1, x1)
        y0 = max(0, y0)
        y1 = min(h - 1, y1)
        if x1 < x0 or y1 < y0:
            continue
        for ry0, ry1 in groups(row_flag, y0, y1):
            if ry1 - ry0 + 1 < win:
                continue
            # 行组内各列是否有照片像素（只看照片行，文字行不参与）
            col_any = base_p[ry0:ry1 + 1, x0:x1 + 1].any(axis=0)
            for cx0, cx1 in groups(col_any, 0, x1 - x0):
                if cx1 - cx0 + 1 < win:
                    continue
                out.append([x0 + cx0, ry0, x0 + cx1, ry1])
    return out


def find_photos(img_small, expect):
    """饱和度密度 + 局部方差 双信号检测照片区域。"""
    arr = np.asarray(img_small.convert("RGB"), dtype=np.float64)
    h, w = arr.shape[:2]
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    mx = arr.max(axis=2)
    mn = arr.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    gray = arr.mean(axis=2)
    win = max(4, min(h, w) // 80)
    sat_density = box_mean(sat, win)
    mean = box_mean(gray, win)
    mean2 = box_mean(gray * gray, win)
    var = mean2 - mean * mean
    # 纸面颜色估计（仅用于调试参考；纸面光照渐变会使全局色差误覆盖整片纸面，不参与判定）
    paper = np.median(arr.reshape(-1, 3), axis=0)
    # 左/右边缘整列红色竖条（菜单装饰边框）：列饱和度密度覆盖率高 → 清除
    colsat = (sat_density > 0.20).mean(axis=0)
    left_clear, right_clear = 0, w - 1
    while left_clear < w // 4 and colsat[left_clear] > 0.75:
        left_clear += 1
    while right_clear > 3 * w // 4 and colsat[right_clear] > 0.75:
        right_clear -= 1
    # 照片像素掩码（仅饱和度信号，供行/列分组用）：照片实心区饱和 0.22+，
    # 菜单文字笔画短、黑墨插画低饱和、纸面白/灰均低于此值。
    base_p = sat_density > 0.22
    mh, mw = int(h * 0.05), int(w * 0.05)
    base_p[:mh, :] = False
    base_p[-mh:, :] = False
    base_p[:, :mw] = False
    base_p[:, -mw:] = False
    base_p[:, :left_clear] = False
    base_p[:, right_clear + 1:] = False
    best = None
    debug = []
    for t_sat in (0.16, 0.22, 0.28):
        for t_var in (200, 400, 700):
            mask = (sat_density > t_sat) | (var > t_var)
            mask[:mh, :] = False
            mask[-mh:, :] = False
            mask[:, :mw] = False
            mask[:, -mw:] = False
            mask[:, :left_clear] = False
            mask[:, right_clear + 1:] = False
            mi = Image.fromarray(mask)
            k = max(3, min(h, w) // 150) | 1
            mi = mi.filter(ImageFilter.MaxFilter(k))
            mi = mi.filter(ImageFilter.MinFilter(k))
            mi = mi.filter(ImageFilter.MaxFilter(5))
            m = np.asarray(mi)
            comps = detect_components(m)
            boxes = []
            for area, x0, y0, x1, y1 in comps:
                bw, bh = x1 - x0 + 1, y1 - y0 + 1
                if area < 0.008 * h * w:
                    continue
                if bw < 0.10 * w or bh < 0.08 * h:
                    continue
                boxes.append([x0, y0, x1, y1])
            merged = []
            for bx in boxes:
                for mb in merged:
                    ix0, iy0 = max(bx[0], mb[0]), max(bx[1], mb[1])
                    ix1, iy1 = min(bx[2], mb[2]), min(bx[3], mb[3])
                    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
                    a1 = (bx[2] - bx[0]) * (bx[3] - bx[1])
                    a2 = (mb[2] - mb[0]) * (mb[3] - mb[1])
                    if inter > 0.15 * min(a1, a2):
                        mb[0] = min(mb[0], bx[0])
                        mb[1] = min(mb[1], bx[1])
                        mb[2] = max(mb[2], bx[2])
                        mb[3] = max(mb[3], bx[3])
                        break
                else:
                    merged.append(list(bx))
            final = split_and_refine(merged, base_p)
            kept = []
            for bx in final:
                bw, bh = bx[2] - bx[0] + 1, bx[3] - bx[1] + 1
                if bw < 0.10 * w or bh < 0.08 * h:
                    continue
                if not (0.45 <= bw / bh <= 2.6):
                    continue
                # 照片得分：框内饱和度密度均值（照片 0.25+，含大段文字的合并框 ~0.15；
                # 不加面积权重，避免「照片+文字列表」的大框压过纯照片框）
                sub = sat_density[max(0, bx[1]):min(h, bx[3] + 1), max(0, bx[0]):min(w, bx[2] + 1)]
                # 高饱和核心：真实菜品照片必有鲜明色块（红油/辣椒/绿叶），
                # 灯光渐变发暗的纸面、文字与黑墨插画区域 sat 最高不过 0.3，以此剔除
                if (sub > 0.40).mean() < 0.08:
                    continue
                score = sub.mean() + 1e-7 * (bw * bh)
                kept.append((score, bx))
            debug.append((t_sat, t_var, len(kept)))
            # 评分优先选阈值组合：数量接近预期且总分高
            if kept:
                kept_sorted = sorted(kept, key=lambda z: -z[0])
                picked = kept_sorted[:expect] if len(kept_sorted) >= expect else kept_sorted
                total = sum(z[0] for z in picked)
                pen = abs(len(kept_sorted) - expect)
                if best is None or (pen < best[3]) or (pen == best[3] and total > best[0]):
                    best = (total, [z[1] for z in picked], (t_sat, t_var), pen, len(kept_sorted))
    if best is None:
        return None, None, debug
    boxes = sorted(best[1], key=lambda bx: (bx[1] + (bx[3] - bx[1]) // 2, bx[0]))
    return boxes, best[2], debug


def main():
    os.makedirs(DISH_DIR, exist_ok=True)
    os.makedirs(PAGE_DIR, exist_ok=True)
    from menu_data import DISHES, PHOTO_INDEX
    names = {n for _, n, _, _, _ in DISHES}
    for _f, dishes in PAGES:
        for d in dishes:
            assert d in names, "菜单数据里没有菜品：" + d

    crops = []
    for pi, (fname, dishes) in enumerate(PAGES, 1):
        src = os.path.join(SRC_DIR, fname)
        img = Image.open(src).convert("RGB")
        W, H = img.size
        scale = 1400.0 / max(W, H)
        small = img.resize((max(1, int(W * scale)), max(1, int(H * scale))), Image.LANCZOS)
        boxes, thr, debug = find_photos(small, len(dishes))
        status = "OK" if boxes else "FAIL"
        print("[%s] %s 预期%d张 检测%d个框 thr=%s" % (status, fname, len(dishes), len(boxes) if boxes else 0, thr))
        if not boxes:
            for td, tv, cnt in debug:
                print("      (dist=%d var=%d) -> %d 框" % (td, tv, cnt))
        if boxes:
            for (x0, y0, x1, y1), dish in zip(boxes, dishes):
                fx, fy = W / small.width, H / small.height
                X0, Y0, X1, Y1 = x0 * fx, y0 * fy, (x1 + 1) * fx, (y1 + 1) * fy
                crop = img.crop((int(X0), int(Y0), int(X1), int(Y1)))
                out = os.path.join(DISH_DIR, PHOTO_INDEX[dish] + ".jpg")
                crop.save(out, quality=88)
                print("    %s -> dishes/%s.jpg (%dx%d)" % (dish, PHOTO_INDEX[dish], crop.width, crop.height))
                crops.append((dish, crop))
        img.save(os.path.join(PAGE_DIR, "%02d.jpg" % pi), quality=90)

    if crops:
        cell = 340
        cols = 4
        rows = (len(crops) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * cell, rows * (cell + 44)), (245, 242, 236))
        draw = ImageDraw.Draw(sheet)
        try:
            font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 26)
        except Exception:
            font = None
        for i, (dish, crop) in enumerate(crops):
            r, c = divmod(i, cols)
            x, y = c * cell, r * (cell + 44)
            th = crop.copy()
            th.thumbnail((cell - 10, cell - 10))
            sheet.paste(th, (x + 5, y + 5))
            draw.text((x + 5, y + cell - 24), dish, fill=(120, 40, 10), font=font)
        sheet_path = os.path.join(BACKEND_DIR, "_crop_check.png")
        sheet.save(sheet_path)
        print("拼贴核对图：", sheet_path)
    print("完成：菜品照片 %d 张 -> %s" % (len(crops), DISH_DIR))


if __name__ == "__main__":
    main()
