# -*- coding: utf-8 -*-
"""全链路冒烟测试：httpx 直接打本地 API，逐项断言。每次运行前重置数据库。"""
import os
import sys

# Windows 下无论控制台编码如何都输出 UTF-8，避免中文乱码/报错（无需 PYTHONUTF8=1）
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import httpx  # noqa: E402

from app.database import Base, engine, SessionLocal, ensure_schema  # noqa: E402
import seed  # noqa: E402

BASE = os.getenv("SMOKE_BASE", "http://127.0.0.1:8000")
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  -- " + str(detail)) if detail and not cond else ""))


def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed.seed_shop(db)
        seed.seed_staff(db)
        seed.seed_categories(db)
        seed.seed_dishes(db)
        seed.seed_ingredients(db)
        seed.seed_bom(db)
        seed.seed_suppliers(db)
        seed.seed_tables(db)
        db.commit()
    finally:
        db.close()
    ensure_schema()  # 外带占位桌台(id=0)：外带单 table_id=0 需满足外键


def main():
    print("重置数据库…")
    reset_db()
    c = httpx.Client(base_url=BASE, timeout=15)
    h = {}

    # 1. 登录
    r = c.post("/api/staff/login", json={"username": "waiter", "password": "123456"})
    check("服务员登录", r.status_code == 200, r.text)
    h["waiter"] = {"Authorization": "Bearer " + r.json()["token"]}

    r = c.post("/api/staff/login", json={"username": "waiter2", "password": "123456"})
    check("服务员2登录(多账号)", r.status_code == 200 and r.json()["staff"]["role"] == "waiter", r.text)
    h["waiter2"] = {"Authorization": "Bearer " + r.json()["token"]}

    r = c.post("/api/staff/login", json={"username": "kitchen", "password": "123456"})
    check("后厨登录", r.status_code == 200, r.text)
    h["kitchen"] = {"Authorization": "Bearer " + r.json()["token"]}

    r = c.post("/api/staff/login", json={"username": "admin", "password": "123456"})
    check("店长登录", r.status_code == 200, r.text)
    h["admin"] = {"Authorization": "Bearer " + r.json()["token"]}

    r = c.post("/api/staff/login", json={"username": "waiter", "password": "bad"})
    check("错误密码拒绝", r.status_code == 401, r.text)

    r = c.post("/api/customer/login", json={"code": "test-openid-001"})
    check("顾客模拟登录", r.status_code == 200, r.text)
    h["cust"] = {"Authorization": "Bearer " + r.json()["token"]}

    # 2. 菜单与桌台
    r = c.get("/api/customer/menu")
    data = r.json()
    check("菜单获取", r.status_code == 200 and len(data["categories"]) == 6, r.text)
    dish_total = sum(len(x["dishes"]) for x in data["categories"])
    check("菜单 98 道菜", dish_total == 98, dish_total)
    check("菜品含 emoji/图片/category_id 字段", all("emoji" in d and "image" in d and "category_id" in d for x in data["categories"] for d in x["dishes"]), data["categories"][0]["dishes"][0])
    dish = {}
    for x in data["categories"]:
        for d in x["dishes"]:
            dish[d["name"]] = d
    check("有照片菜品带图片URL", any(d["image"].startswith("/web/uploads/dishes/") for d in dish.values()), "")

    r = c.get("/api/customer/tables/by-scene/A01")
    check("桌台码解析 A01", r.status_code == 200 and r.json()["name"] == "A01", r.text)
    a01_id = r.json()["id"]

    r = c.get("/api/customer/tables/by-scene/B03")
    check("停用桌台 B03 拒绝", r.status_code == 400, r.text)

    r = c.get("/api/customer/tables")
    check("桌台列表公开接口", r.status_code == 200 and any(t["name"] == "A01" for t in r.json()), r.text)

    # 3. 顾客下单（含选项）
    order_body = {
        "table_id": a01_id,
        "items": [
            {"dish_id": dish["炝锅鱼"]["id"], "qty": 1, "options": {"辣度": "中辣"}},  # 炝锅鱼×1
            {"dish_id": dish["回锅肉"]["id"], "qty": 2},                                # 回锅肉×2
            {"dish_id": dish["虎皮豆腐"]["id"], "qty": 2},                              # 虎皮豆腐×2
        ],
        "remark": "米饭稍后上",
        "people": 3,
        "utensils": 0,
    }
    r = c.post("/api/customer/orders", json=order_body, headers=h["cust"])
    check("顾客下单 158 元", r.status_code == 200 and r.json()["total_amount"] == 158.0, r.text)
    order_id = r.json()["id"]
    check("下单后待确认", r.json()["status"] == "pending_confirm", r.text)
    # 餐具费：每人 1 元，随人数累计进应收（3 人 = 3 元）
    check("餐具费=人数×1 计入应收", r.json()["utensil_fee"] == 3.0 and r.json()["receivable"] == 161.0, r.text)

    r = c.post("/api/customer/orders", json={**order_body, "items": [{"dish_id": 999, "qty": 1}]}, headers=h["cust"])
    check("无效菜品拒绝", r.status_code == 400, r.text)

    # 4. 服务员确认
    r = c.get("/api/waiter/queue", headers=h["waiter"])
    check("待确认队列含新单", r.status_code == 200 and any(o["id"] == order_id for o in r.json()), r.text)

    r = c.post("/api/waiter/orders/%d/confirm" % order_id, headers=h["waiter"])
    check("服务员确认", r.status_code == 200 and r.json()["status"] == "confirmed", r.text)

    r = c.get("/api/waiter/settle-queue", headers=h["waiter"])
    check("收银台队列含已确认未结账订单", r.status_code == 200 and any(o["id"] == order_id for o in r.json()), r.text)

    r = c.get("/api/kds/columns", headers=h["kitchen"])
    cols = r.json()
    check("KDS 待出餐栏含订单", any(o["id"] == order_id for o in cols["new"]), cols)
    kds_order = next(o for o in cols["new"] if o["id"] == order_id)
    check("KDS 明细含选项", any(i["options"].get("辣度") == "中辣" for i in kds_order["items"]), kds_order)

    # 5. 逐道出餐 → 出餐中 → 已出餐
    item_map = {i["name"]: i for i in kds_order["items"]}
    r = c.post("/api/kds/items/%d/out" % item_map["炝锅鱼"]["id"], headers=h["kitchen"])
    cols = c.get("/api/kds/columns", headers=h["kitchen"]).json()
    check("单品出餐后进「出餐中」", any(o["id"] == order_id for o in cols["cooking"]) and not any(o["id"] == order_id for o in cols["new"]), "")

    # 幂等：重复出餐不重复扣库存
    r = c.post("/api/kds/items/%d/out" % item_map["炝锅鱼"]["id"], headers=h["kitchen"])
    check("重复出餐幂等", r.status_code == 200 and r.json()["status"] == "out", r.text)

    c.post("/api/kds/items/%d/out" % item_map["回锅肉"]["id"], headers=h["kitchen"])
    cols = c.get("/api/kds/columns", headers=h["kitchen"]).json()
    check("部分出完仍在「出餐中」", any(o["id"] == order_id for o in cols["cooking"]), "")

    c.post("/api/kds/items/%d/out" % item_map["虎皮豆腐"]["id"], headers=h["kitchen"])
    cols = c.get("/api/kds/columns", headers=h["kitchen"]).json()
    check("整单出完进「已出餐」", any(o["id"] == order_id for o in cols["done"]), "")

    r = c.get("/api/customer/orders/%d" % order_id, headers=h["cust"])
    prog = r.json()
    check("顾客端三步进度=已出餐", prog["status"] == "served" and [s["done"] for s in prog["steps"]] == [True, True, True], prog)

    # 6. 补单加菜 → 服务员确认 → KDS 标「加」
    r = c.post("/api/customer/orders/%d/add" % order_id, json={"items": [{"dish_id": dish["香辣羊肚"]["id"], "qty": 1}]}, headers=h["cust"])
    check("补单加菜（香辣羊肚）", r.status_code == 200 and r.json()["total_amount"] == 216.0
          and r.json()["utensil_fee"] == 3.0 and r.json()["receivable"] == 219.0, r.text)

    r = c.get("/api/waiter/add-queue", headers=h["waiter"])
    check("加菜待确认队列", r.status_code == 200 and any(o["id"] == order_id for o in r.json()), r.text)

    cols = c.get("/api/kds/columns", headers=h["kitchen"]).json()
    kds_order = next(o for o in cols["done"] if o["id"] == order_id)
    check("加菜未确认前不出现在 KDS", all(i["name"] != "香辣羊肚" for i in kds_order["items"]), kds_order["items"])

    r = c.post("/api/waiter/orders/%d/confirm-add" % order_id, headers=h["waiter"])
    check("服务员确认加菜", r.status_code == 200 and r.json()["status"] == "confirmed", r.text)

    # V6.0：加菜独立成卡出现在「待出餐」，原单不被拉回「出餐中」
    cols = c.get("/api/kds/columns", headers=h["kitchen"]).json()
    add_card = next((o for o in cols["new"] if o["id"] == order_id and o.get("is_add_card")), None)
    check("加菜单独立进待出餐", add_card is not None and add_card["table_name"] == kds_order["table_name"], cols)
    add_item = next((i for i in (add_card or {}).get("items", []) if i["name"] == "香辣羊肚"), None)
    check("加菜单标「加」", add_item is not None and add_item["is_add"] is True, add_card)
    check("原单留在已出餐且带加菜单标记", any(o["id"] == order_id and not o.get("is_add_card") and o.get("has_add_card") for o in cols["done"]), cols["done"])

    if add_item:
        c.post("/api/kds/items/%d/out" % add_item["id"], headers=h["kitchen"])
    cols = c.get("/api/kds/columns", headers=h["kitchen"]).json()
    check("加菜单出完自动并入(待出餐无加菜单)", not any(o["id"] == order_id and o.get("is_add_card") for o in cols["new"]), cols["new"])
    merged = next((o for o in cols["done"] if o["id"] == order_id), None)
    check("并入后原单含加菜明细", merged is not None and any(i["name"] == "香辣羊肚" and i["is_add"] for i in merged["items"]), merged)

    # 7. 结账（后台收银，服务员端无结账能力）→ 待清台 → 顾客被拒 → 代客下单自动解除
    # 只传优惠：实收自动补齐 = 应收 − 优惠（优惠 + 实收恒等于应收；应收 219 = 菜品 216 + 餐具费 3）
    r = c.post("/api/admin/orders/%d/settle" % order_id, json={"method": "wechat", "discount": 10}, headers=h["admin"])
    check("微信收款·优惠10·实收自动209", r.status_code == 200 and r.json()["status"] == "paid"
          and r.json()["pay_method"] == "wechat" and r.json()["discount"] == 10.0
          and r.json()["utensil_fee"] == 3.0 and r.json()["receivable"] == 219.0
          and r.json()["paid_amount"] == 209.0, r.text)

    r = c.post("/api/waiter/orders/%d/settle" % order_id, json={"method": "cash"}, headers=h["waiter"])
    check("服务员端结账接口已移除(404)", r.status_code == 404, r.text)

    r = c.get("/api/waiter/settle-queue", headers=h["waiter"])
    check("结账后移出收银台队列", r.status_code == 200 and not any(o["id"] == order_id for o in r.json()), r.text)

    # 7.5 已出餐页数据源：当日整单出完的订单（含已结账），按出完时间倒序
    r = c.get("/api/kds/done", headers=h["kitchen"])
    done_list = r.json()
    d0 = next((o for o in done_list if o["id"] == order_id), None)
    check("已出餐页含当日已结账订单", r.status_code == 200 and d0 is not None and d0["paid"] is True
          and bool(d0["finished_at"]), done_list)
    check("已出餐页按出完时间倒序", all(done_list[i]["finished_at"] >= done_list[i + 1]["finished_at"]
          for i in range(len(done_list) - 1)), done_list)

    r = c.post("/api/customer/orders", json=order_body, headers=h["cust"])
    check("待清台顾客扫码下单被拒", r.status_code == 400 and "待清台" in (r.json().get("detail") or ""), r.text)

    r = c.get("/api/waiter/tables", headers=h["waiter"])
    a01 = next(t for t in r.json() if t["id"] == a01_id)
    check("被拒后仍为待清台", a01["state"] == "dirty", a01)

    r = c.post("/api/waiter/orders", json={
        "table_id": a01_id,
        "items": [{"dish_id": dish["干炸里脊"]["id"], "qty": 1}],
        "remark": "", "people": 1, "utensils": 0,
    }, headers=h["waiter"])
    check("待清台代客下单成功", r.status_code == 200 and r.json()["status"] == "confirmed", r.text)

    a01 = next(t for t in c.get("/api/waiter/tables", headers=h["waiter"]).json() if t["id"] == a01_id)
    check("代客下单自动解除待清台", a01["state"] == "dining", a01)

    r = c.post("/api/waiter/tables/%d/clean" % a01_id, headers=h["waiter"])
    check("清台", r.status_code == 200 and r.json()["dirty"] is False, r.text)

    # 8. BOM 扣库存核对（虎皮豆腐：嫩豆腐 300g×1.03×2 份 = 618）
    r = c.get("/api/admin/ingredients", headers=h["admin"])
    tofu = next(i for i in r.json() if i["name"] == "嫩豆腐")
    check("BOM 扣库存(含损耗 618g)", abs(tofu["stock"] - (12000 - 618)) < 0.01, tofu)

    r = c.get("/api/admin/inventory/logs", headers=h["admin"])
    check("库存流水已记录", r.status_code == 200 and len(r.json()) >= 4, len(r.json()))

    # 9. 代客下单（提交即已确认）
    r = c.post("/api/waiter/orders", json={
        "table_id": 2,
        "items": [{"dish_id": dish["干炸里脊"]["id"], "qty": 1}],
        "remark": "", "people": 2, "utensils": 0,
    }, headers=h["waiter"])
    check("代客下单直接已确认", r.status_code == 200 and r.json()["status"] == "confirmed"
          and r.json()["utensil_fee"] == 2.0, r.text)
    guest_id = r.json()["id"]
    cols = c.get("/api/kds/columns", headers=h["kitchen"]).json()
    check("代客下单直达 KDS", any(o["id"] == guest_id for o in cols["new"]), "")

    # 9.5 代客加菜（服务员替顾客加菜，直达 KDS 标「加」，负载带菜品分类）
    r = c.post("/api/waiter/orders/%d/add" % guest_id, json={"items": [{"dish_id": dish["羊肉烩面"]["id"], "qty": 2}]}, headers=h["waiter"])
    check("代客加菜直达后厨", r.status_code == 200 and r.json()["total_amount"] == 66.0
          and r.json()["utensil_fee"] == 2.0 and r.json()["receivable"] == 68.0, r.text)
    cols = c.get("/api/kds/columns", headers=h["kitchen"]).json()
    ko = next((o for o in cols["new"] if o["id"] == guest_id and o.get("is_add_card")), None)
    noodle = next((i for i in (ko or {}).get("items", []) if i["name"] == "羊肉烩面"), None)
    check("KDS 加菜单标「加」且带主食分类", noodle is not None and noodle["is_add"] is True and noodle["is_staple"] is True
          and noodle["category_name"] == "主食", noodle)

    r = c.get("/api/kds/done", headers=h["kitchen"])
    check("未整单出完不进已出餐页", not any(o["id"] == guest_id for o in r.json()), r.json())

    # 9.6 结账优惠校验（优惠超应收/实收超应收被拒；支付宝收款）
    r = c.post("/api/admin/orders/%d/settle" % guest_id, json={"method": "cash", "discount": 100}, headers=h["admin"])
    check("优惠超应收被拒", r.status_code == 400, r.text)

    r = c.post("/api/admin/orders/%d/settle" % guest_id, json={"method": "cash", "paid_amount": 999}, headers=h["admin"])
    check("实收超应收被拒", r.status_code == 400, r.text)

    # 只传实收（优惠字段冲突传 0）：优惠自动调整 = 应收 − 实收，保证优惠 + 实收 = 应收（应收 68 = 菜品 66 + 餐具费 2）
    r = c.post("/api/admin/orders/%d/settle" % guest_id, json={"method": "alipay", "discount": 0, "paid_amount": 60}, headers=h["admin"])
    check("支付宝收款·实收60自动优惠8", r.status_code == 200 and r.json()["pay_method"] == "alipay"
          and r.json()["discount"] == 8.0 and r.json()["paid_amount"] == 60.0
          and r.json()["receivable"] == 68.0, r.text)

    r = c.get("/api/kds/done", headers=h["kitchen"])
    check("已结账但未出餐不进已出餐页", not any(o["id"] == guest_id for o in r.json()), r.json())

    # 9.7 外带单：无需桌台，服务员直接下单直达后厨；结账不产生待清台
    before_dirty = {t["name"] for t in c.get("/api/waiter/tables", headers=h["waiter"]).json() if t["state"] == "dirty"}
    r = c.post("/api/waiter/orders", json={
        "takeout": True,
        "items": [{"dish_id": dish["回锅肉"]["id"], "qty": 1}],
        "remark": "打包", "people": 1, "utensils": 1,
    }, headers=h["waiter"])
    check("外带下单成功(无需桌台)", r.status_code == 200 and r.json()["is_takeout"] is True
          and r.json()["table_name"] == "外带" and r.json()["status"] == "confirmed"
          and r.json()["total_amount"] == 30.0 and r.json()["utensil_fee"] == 1.0
          and r.json()["receivable"] == 31.0, r.text)
    tk_id = r.json()["id"]

    cols = c.get("/api/kds/columns", headers=h["kitchen"]).json()
    tk_kds = next((o for o in cols["new"] if o["id"] == tk_id), None)
    check("外带单直达 KDS 且桌台名为外带", tk_kds is not None and tk_kds["table_name"] == "外带", cols["new"])

    c.post("/api/kds/items/%d/out" % tk_kds["items"][0]["id"], headers=h["kitchen"])
    r = c.post("/api/admin/orders/%d/settle" % tk_id, json={"method": "cash", "discount": 0, "paid_amount": 31}, headers=h["admin"])
    check("外带结账成功", r.status_code == 200 and r.json()["status"] == "paid"
          and r.json()["paid_amount"] == 31.0 and r.json()["utensil_fee"] == 1.0, r.text)
    after_dirty = {t["name"] for t in c.get("/api/waiter/tables", headers=h["waiter"]).json() if t["state"] == "dirty"}
    check("外带结账不产生待清台", after_dirty == before_dirty, (before_dirty, after_dirty))

    r = c.get("/api/kds/done", headers=h["kitchen"])
    d1 = next((o for o in r.json() if o["id"] == tk_id), None)
    check("已出餐页含当日外带单", d1 is not None and d1["paid"] is True, r.json())

    # 9.75 外带打包盒计费：不选人数（系统固定 1 人），打包盒数量 × 1 元计入应收，明细标注「打包盒费」
    r = c.post("/api/waiter/orders", json={
        "takeout": True,
        "items": [{"dish_id": dish["回锅肉"]["id"], "qty": 1}],
        "remark": "多盒打包", "utensils": 3,
    }, headers=h["waiter"])
    check("外带多打包盒计费(3盒×1元)", r.status_code == 200 and r.json()["people"] == 1
          and r.json()["utensils"] == 3 and r.json()["utensil_fee"] == 3.0
          and r.json()["fee_label"] == "打包盒费" and r.json()["utensil_label"] == "打包盒"
          and r.json()["total_amount"] == 30.0 and r.json()["receivable"] == 33.0, r.text)
    tk2_id = r.json()["id"]
    r = c.post("/api/admin/orders/%d/settle" % tk2_id, json={"method": "wechat", "discount": 0, "paid_amount": 33}, headers=h["admin"])
    check("外带多盒结账(微信33)", r.status_code == 200 and r.json()["paid_amount"] == 33.0
          and r.json()["pay_method"] == "wechat" and r.json()["fee_label"] == "打包盒费", r.text)

    # 9.8 人数/餐具随时修改：确认后也可改；待确认阶段可同时调菜品
    r = c.post("/api/waiter/orders", json={
        "table_id": 3,
        "items": [{"dish_id": dish["羊肉烩面"]["id"], "qty": 1}],
        "remark": "", "people": 2, "utensils": 2,
    }, headers=h["waiter"])
    check("代客下单记录人数/餐具", r.status_code == 200 and r.json()["people"] == 2
          and r.json()["utensils"] == 2 and r.json()["utensil_fee"] == 2.0, r.text)
    mid = r.json()["id"]

    r = c.post("/api/waiter/orders/%d/modify" % mid, json={"people": 5, "utensils": 5, "items": []}, headers=h["waiter"])
    check("已确认订单修改人数/餐具", r.status_code == 200 and r.json()["people"] == 5
          and r.json()["utensils"] == 5 and r.json()["utensil_fee"] == 5.0, r.text)

    r = c.post("/api/customer/orders", json={"table_id": 4, "items": [{"dish_id": dish["香辣羊肚"]["id"], "qty": 1}],
                                             "remark": "", "people": 1, "utensils": 1}, headers=h["cust"])
    pid = r.json()["id"]
    o4 = c.get("/api/waiter/orders/%d" % pid, headers=h["waiter"]).json()
    it5b = next(i for i in o4["items"] if i["dish_id"] == dish["香辣羊肚"]["id"])
    r = c.post("/api/waiter/orders/%d/modify" % pid, json={
        "people": 3, "utensils": 6,
        "items": [{"item_id": it5b["id"], "qty": 2}],
    }, headers=h["waiter"])
    check("待确认修改：人数餐具+调数量", r.status_code == 200 and r.json()["people"] == 3
          and r.json()["utensils"] == 6 and r.json()["total_amount"] == 116.0
          and r.json()["utensil_fee"] == 3.0, r.text)
    c.post("/api/customer/orders/%d/cancel" % pid, json={"reason": "测试"}, headers=h["cust"])

    # 10. 库存预警 + 一键采购 + 入库
    r = c.get("/api/admin/inventory/warnings", headers=h["admin"])
    ws = r.json()
    check("红黄两级预警", r.status_code == 200 and any(w["name"] == "花椒" and w["level"] == "red" for w in ws)
          and any(w["name"] == "干辣椒" and w["level"] == "yellow" for w in ws), ws)

    r = c.post("/api/admin/purchases/generate-from-warnings?supplier_id=1", headers=h["admin"])
    check("一键生成采购单", r.status_code == 200 and len(r.json()["items"]) >= 2, r.text)
    po_id = r.json()["id"]

    r = c.post("/api/admin/purchases/%d/inbound" % po_id, headers=h["admin"])
    check("采购入库", r.status_code == 200 and r.json()["status"] == "inbound", r.text)

    r = c.get("/api/admin/inventory/warnings", headers=h["admin"])
    ws2 = r.json()
    check("入库后预警解除", not any(w["name"] in ("花椒", "干辣椒") for w in ws2), ws2)

    # 11. 统计与损耗对比
    r = c.get("/api/admin/dashboard", headers=h["admin"])
    check("工作台指标", r.status_code == 200 and r.json()["today_orders"] >= 2
          and r.json()["today_revenue"] >= 300.0, r.text)

    r = c.get("/api/admin/stats/top-dishes", headers=h["admin"])
    check("菜品排行", r.status_code == 200 and len(r.json()) >= 3, r.text)

    r = c.get("/api/admin/stats/loss-compare", headers=h["admin"])
    check("标准vs实际损耗", r.status_code == 200 and len(r.json()) > 0, r.text)

    # 12. AI 兜底（未配置密钥走规则模板）
    r = c.post("/api/admin/ai/reports", json={"type": "daily"}, headers=h["admin"])
    check("AI 日报(规则兜底)", r.status_code == 200 and "经营" in r.json()["content"], r.text)

    r = c.post("/api/admin/ai/chat", json={"question": "今天营业额多少？"}, headers=h["admin"])
    check("AI 对话(规则兜底)", r.status_code == 200 and "今日" in r.json()["answer"], r.text)

    r = c.get("/api/admin/ai/reports", headers=h["admin"])
    check("报告归档", r.status_code == 200 and len(r.json()) >= 1, r.text)

    # 13. 支付预留
    r = c.post("/api/payment/prepay", headers=h["cust"])
    check("支付接口预留(501)", r.status_code == 501, r.text)

    # 14. 顾客取消
    r = c.post("/api/customer/orders", json={"table_id": 3, "items": [{"dish_id": dish["香辣羊肚"]["id"], "qty": 1}],
                                             "remark": "", "people": 1, "utensils": 0}, headers=h["cust"])
    oid2 = r.json()["id"]
    r = c.post("/api/customer/orders/%d/cancel" % oid2, json={"reason": "点错了"}, headers=h["cust"])
    check("顾客取消订单", r.status_code == 200 and r.json()["status"] == "cancelled", r.text)

    # 14.5 待确认修改：调数量 + 新增菜品（本轮新能力）
    r = c.post("/api/customer/orders", json={"table_id": 3, "items": [{"dish_id": dish["香辣羊肚"]["id"], "qty": 1}],
                                             "remark": "", "people": 2, "utensils": 2}, headers=h["cust"])
    oid3 = r.json()["id"]
    o3 = c.get("/api/waiter/orders/%d" % oid3, headers=h["waiter"]).json()
    it5 = next(i for i in o3["items"] if i["dish_id"] == dish["香辣羊肚"]["id"])
    r = c.post("/api/waiter/orders/%d/modify" % oid3, json={
        "items": [
            {"item_id": it5["id"], "qty": 2},
            {"dish_id": dish["干炸里脊"]["id"], "qty": 1, "options": {}},
        ],
    }, headers=h["waiter"])
    check("待确认修改：调数量+新增菜品", r.status_code == 200 and r.json()["total_amount"] == 152.0
          and r.json()["utensil_fee"] == 2.0
          and len(r.json()["items"]) == 2 and any(i["name"] == "干炸里脊" for i in r.json()["items"]), r.text)
    c.post("/api/customer/orders/%d/cancel" % oid3, json={"reason": "测试"}, headers=h["cust"])

    # 16. 免单：实收 0 元，必填免单原因；按应收金额计入营业额（免单金额单列）
    dash_before = c.get("/api/admin/dashboard", headers=h["admin"]).json()
    r = c.post("/api/waiter/orders", json={
        "table_id": 5,
        "items": [{"dish_id": dish["干炸里脊"]["id"], "qty": 1}],
        "remark": "免单测试", "people": 2, "utensils": 0,
    }, headers=h["waiter"])
    check("免单前代客下单(应收38)", r.status_code == 200 and r.json()["receivable"] == 38.0, r.text)
    free_id = r.json()["id"]

    r = c.post("/api/admin/orders/%d/settle" % free_id, json={"method": "free"}, headers=h["admin"])
    check("免单无原因被拒", r.status_code == 400 and "免单原因" in (r.json().get("detail") or ""), r.text)

    r = c.post("/api/admin/orders/%d/settle" % free_id, json={"paid_amount": 0}, headers=h["admin"])
    check("实收0无原因同样被拒", r.status_code == 400, r.text)

    r = c.post("/api/admin/orders/%d/settle" % free_id, json={"method": "free", "settle_note": "老板请客"}, headers=h["admin"])
    check("免单成功(实收0·全额冲抵·原因留存)", r.status_code == 200 and r.json()["status"] == "paid"
          and r.json()["pay_method"] == "free" and r.json()["paid_amount"] == 0.0
          and r.json()["discount"] == 38.0 and r.json()["receivable"] == 38.0
          and r.json()["settle_note"] == "老板请客", r.text)

    dash = c.get("/api/admin/dashboard", headers=h["admin"]).json()
    check("免单按应收计入营业额", abs(dash["today_revenue"] - (dash_before["today_revenue"] + 38.0)) < 0.01, dash)
    check("工作台单列今日免单", dash.get("today_free", 0) >= 38.0, dash)

    # 17. 开门 + 结单对账：系统收款自动汇总 vs 钱箱实点，差额可对平
    r = c.get("/api/admin/day/status", headers=h["admin"])
    check("营业状态(未开门)", r.status_code == 200 and r.json()["opened"] is None, r.text)

    r = c.post("/api/admin/day/open", json={"change": 100}, headers=h["admin"])
    o = r.json()
    check("开门登记(零钱箱100·日期星期·天气)", r.status_code == 200 and o["open_change"] == 100.0
          and bool(o["biz_date"]) and bool(o["weekday"]) and o["weekday"].startswith("星期")
          and "weather" in o and o.get("city") == "山西运城盐湖区", o)
    check("开门提示注意事项", isinstance(o.get("attentions"), list) and len(o["attentions"]) >= 2, o.get("attentions"))

    r = c.get("/api/admin/day/status", headers=h["admin"])
    check("营业状态(已开门·含实时汇总)", r.status_code == 200 and r.json()["opened"] is not None
          and r.json()["live"]["order_count"] >= 4, r.text)

    # 当日已结账：现金31(外带) + 微信209+33 + 支付宝60 + 免单38（按应收单列），共 5 单
    r = c.post("/api/admin/day/close", json={"cash_box": 131, "note": "晚班结单"}, headers=h["admin"])
    cd = r.json()["closed"]
    check("结单汇总(现金31·微信242·支付宝60·免单38)", r.status_code == 200
          and cd["sys_cash"] == 31.0 and cd["sys_wechat"] == 242.0 and cd["sys_alipay"] == 60.0
          and cd["free_amount"] == 38.0 and cd["sys_total"] == 333.0 and cd["order_count"] == 5, r.text)
    check("结单差额=钱箱−零钱−系统现金(对平)", cd["diff"] == 0.0
          and r.json()["expected_cash_box"] == 131.0, r.text)

    r = c.post("/api/admin/day/close", json={"cash_box": 135, "note": "重核"}, headers=h["admin"])
    check("重复结单更新对账(差额+4)", r.status_code == 200 and r.json()["closed"]["diff"] == 4.0, r.text)

    r = c.get("/api/admin/day/status", headers=h["admin"])
    st = r.json()
    check("结单后营业状态(近7日含今日)", st["closed"] is not None and st["closed"]["diff"] == 4.0
          and any(x["biz_date"] == st["biz_date"] for x in st["history"]), st)

    # 15. 权限
    r = c.get("/api/kds/columns", headers=h["waiter"])
    check("服务员无权访问 KDS", r.status_code == 403, r.text)
    r = c.get("/api/admin/dishes", headers=h["cashier"] if False else h["waiter"])
    check("服务员无权访问菜品管理", r.status_code == 403, r.text)

    print("\n共 %d 项通过，%d 项失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)


if __name__ == "__main__":
    main()
