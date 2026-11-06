# -*- coding: utf-8 -*-
"""import_menu.py 隔离测试：构造老库（川香居演示数据 + 历史订单），导入真实菜单后逐项断言。"""
import os
import sys
from decimal import Decimal

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import Base, engine, SessionLocal  # noqa: E402
from app import models  # noqa: E402
import import_menu  # noqa: E402
from menu_data import CATEGORIES, DISHES, PHOTO_INDEX, OPTION_TEMPLATES, BOM  # noqa: E402

D = Decimal
FAILS = []


def check(cond, msg):
    print(("OK  " if cond else "FAIL") + " " + msg)
    if not cond:
        FAILS.append(msg)


def build_legacy(db):
    # 老分类：招牌菜/热菜/凉菜/汤品/主食（无鱼类）
    cats = {
        "招牌菜": models.Category(name="招牌菜", sort=0),
        "热菜": models.Category(name="热菜", sort=1),
        "凉菜": models.Category(name="凉菜", sort=2),
        "汤品": models.Category(name="汤品", sort=3),
        "主食": models.Category(name="主食", sort=4),
    }
    for c in cats.values():
        db.add(c)
    db.flush()
    legacy = [
        ("麻婆豆腐", "招牌菜", 25), ("水煮鱼", "招牌菜", 58),
        ("回锅肉", "招牌菜", 28),   # 与真实菜单重名：应被更新（价格30→热菜）
        ("辣子鸡丁", "热菜", 45), ("干煸四季豆", "热菜", 22),
        ("酸辣汤", "汤品", 15), ("蛋炒饭", "主食", 12),
    ]
    dish_rows = {}
    for name, cat, price in legacy:
        d = models.Dish(category_id=cats[cat].id, name=name, price=D(str(price)),
                        description="老菜", sales=10, emoji="", image="")
        db.add(d)
        db.flush()
        dish_rows[name] = d
    # 老原料：猪肉/豆腐/大米（真实 BOM 所需原料大多缺失 → 应自动补齐）
    ing_rows = {}
    for name, unit in [("猪肉", "g"), ("豆腐", "g"), ("大米", "g")]:
        i = models.Ingredient(name=name, unit=unit, stock=D("1000"),
                              safe_stock=D("200"), ref_price=D("0.02"))
        db.add(i)
        db.flush()
        ing_rows[name] = i
    # 回锅肉已有旧配方（猪肉）→ 导入后应保持不动
    db.add(models.DishIngredient(dish_id=dish_rows["回锅肉"].id,
                                 ingredient_id=ing_rows["猪肉"].id,
                                 quantity=D("200"), loss_rate=D("5")))
    # 桌台 + 历史订单（麻婆豆腐）→ 下架后订单与明细应完整保留
    t = models.DiningTable(id=1, name="A01", area="大厅", seats=4,
                           qr_scene="A01", enabled=True, dirty=False)
    db.add(t)
    db.flush()
    o = models.Order(order_no="L20261025001", table_id=1, status=models.ORDER_PAID,
                     total_amount=D("25"), people=2, utensils=2,
                     pay_status=models.PAY_PAID, pay_method="cash", paid_amount=D("25"))
    db.add(o)
    db.flush()
    db.add(models.OrderItem(order_id=o.id, dish_id=dish_rows["麻婆豆腐"].id,
                            name="麻婆豆腐", price=D("25"), qty=1, status=models.ITEM_OUT))
    db.commit()
    print("老库构造完成：分类%d 菜品%d 原料%d 订单1" % (len(cats), len(legacy), len(ing_rows)))


def main():
    # 测试库由环境变量指定（绝不碰正式 restaurant.db）
    db_path = os.path.basename(settings_dummy())
    print("测试库：", db_path)
    if db_path != "import_test.db":
        print("!! 请用 DATABASE_URL=sqlite:///./import_test.db 运行")
        sys.exit(1)
    if os.path.exists(db_path):
        os.remove(db_path)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        build_legacy(db)
    finally:
        db.close()

    import_menu.main()

    db = SessionLocal()
    try:
        # 1. 分类：招牌菜→本店特色、汤品→汤类 改名不新增，鱼类补齐，sort 连续
        cats = {c.name: c.sort for c in db.query(models.Category).all()}
        check(set(cats.keys()) == set(CATEGORIES),
              "分类恰为 %s（改名不产生重复，实际=%s）" % ("/".join(CATEGORIES), "/".join(sorted(cats))))
        check([cats.get(n) for n in CATEGORIES] == list(range(len(CATEGORIES))),
              "分类 sort 顺序 0..5")

        # 2. 菜品：98 道全部上架，旧菜全部下架
        on = db.query(models.Dish).filter_by(status=models.DISH_ON).all()
        real_names = {d[1] for d in DISHES}
        check(len(on) == len(DISHES) and {d.name for d in on} == real_names,
              "上架菜品 %d 道且与真实菜单一致（实际=%d）" % (len(DISHES), len(on)))
        for name in ["麻婆豆腐", "水煮鱼", "辣子鸡丁", "干煸四季豆", "酸辣汤", "蛋炒饭"]:
            d = db.query(models.Dish).filter_by(name=name).first()
            check(d is not None and d.status == models.DISH_OFF, "旧菜 %s 已下架" % name)

        # 3. 重名菜回锅肉：更新为真实价格/分类/图片
        hg = db.query(models.Dish).filter_by(name="回锅肉").first()
        check(hg.status == models.DISH_ON and hg.price == D("30"), "回锅肉更新为 30 元上架")
        hg_cat = db.query(models.Category).get(hg.category_id)
        check(hg_cat.name == "热菜", "回锅肉归入热菜")
        check(hg.image == "/web/uploads/dishes/13.jpg", "回锅肉图片 13.jpg")

        # 4. 图片：21 道有照片，其余留空
        with_img = db.query(models.Dish).filter(
            models.Dish.image != "", models.Dish.status == models.DISH_ON).all()
        check(len(with_img) == 21, "带照片菜品 21 道（实际=%d）" % len(with_img))
        check(all(d.image == "/web/uploads/dishes/%s.jpg" % PHOTO_INDEX[d.name]
                  for d in with_img), "图片路径与 PHOTO_INDEX 一致")
        no_img = db.query(models.Dish).filter(
            models.Dish.image == "", models.Dish.status == models.DISH_ON).all()
        check(len(no_img) == len(DISHES) - 21, "无照片菜品 %d 道留空" % (len(DISHES) - 21))

        # 5. 可选配置
        qgy = db.query(models.Dish).filter_by(name="炝锅鱼").first()
        jme = db.query(models.Dish).filter_by(name="芥末木耳").first()
        check(qgy.option_template == OPTION_TEMPLATES["炝锅鱼"], "炝锅鱼辣度选项")
        check(jme.option_template == OPTION_TEMPLATES["芥末木耳"], "芥末木耳芥末选项")

        # 6. 历史订单保留
        o = db.query(models.Order).filter_by(order_no="L20261025001").first()
        item = db.query(models.OrderItem).filter_by(order_id=o.id).first() if o else None
        check(o is not None and item is not None and item.name == "麻婆豆腐"
              and item.dish_id is not None, "历史订单明细完整（麻婆豆腐已下架但订单可查）")

        # 7. 原料：老原料保留 + 羊肉/羊杂 + 缺失 BOM 原料自动补齐
        ing_names = {i.name for i in db.query(models.Ingredient).all()}
        need = {"羊肉", "羊杂"} | {n for _d, items in BOM.items() for n, _q, _l in items}
        check(need <= ing_names, "BOM 所需原料齐全（缺失=%s）" % (need - ing_names))
        check({"猪肉", "豆腐", "大米"} <= ing_names, "老原料保留")

        # 8. BOM：回锅肉旧配方不动，其余补齐
        hg_bom = db.query(models.DishIngredient).filter_by(dish_id=hg.id).all()
        ing1 = db.query(models.Ingredient).get(hg_bom[0].ingredient_id)
        check(len(hg_bom) == 1 and ing1.name == "猪肉", "回锅肉已有配方未被覆盖")
        # 对每个 BOM 菜检查至少有一条配方（回锅肉除外）
        dm = {d.name: d.id for d in db.query(models.Dish).all()}
        missing = [dn for dn in BOM if dn != "回锅肉" and
                   db.query(models.DishIngredient).filter_by(dish_id=dm[dn]).count() == 0]
        check(not missing, "BOM 菜品配方全部补齐（缺=%s）" % missing)

        # 9. 幂等：再跑一次不应翻倍
        import_menu.main()
        db.expire_all()
        check(db.query(models.Category).count() == len(CATEGORIES), "幂等：分类数不变")
        check(db.query(models.Dish).filter_by(status=models.DISH_ON).count() == len(DISHES),
              "幂等：上架菜品数不变")
    finally:
        db.close()

    print()
    if FAILS:
        print("共 %d 项失败" % len(FAILS))
        sys.exit(1)
    print("全部断言通过 ✓")


def settings_dummy():
    from app.config import settings
    return settings.database_url


if __name__ == "__main__":
    main()
