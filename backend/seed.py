"""种子数据：门店、员工、分类、菜品（含可选配置）、原料、BOM、供应商、桌台。幂等，可重复执行。"""
import os
import sys
from decimal import Decimal

# Windows 下无论控制台编码如何都输出 UTF-8，避免中文乱码/报错（无需 PYTHONUTF8=1）
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import Base, engine, SessionLocal, ensure_schema  # noqa: E402
from app import models  # noqa: E402
from app.security import hash_password  # noqa: E402
from menu_data import CATEGORIES, DISHES, PHOTO_INDEX, OPTION_TEMPLATES, EXTRA_INGREDIENTS, BOM  # noqa: E402

D = Decimal


def seed_shop(db):
    if db.query(models.Shop).first():
        return
    db.add(models.Shop(
        name="川香居 · 家常菜",
        phone="028-88886666",
        address="成都市锦江区春熙路 88 号 2 层",
        business_hours="10:00 - 22:00",
        notice="堂食扫码点餐 · 请至收银台结账",
    ))


def seed_staff(db):
    rows = [
        ("admin", "王强", models.ROLE_ADMIN, "店长"),
        ("cashier", "赵敏", models.ROLE_CASHIER, "收银"),
        ("waiter", "李梅", models.ROLE_WAITER, "服务员"),
        ("waiter2", "周芳", models.ROLE_WAITER, "服务员2"),
        ("waiter3", "孙丽", models.ROLE_WAITER, "服务员3"),
        ("kitchen", "陈大厨", models.ROLE_KITCHEN, "后厨"),
    ]
    existing = {u for (u,) in db.query(models.Staff.username).all()}
    for username, name, role, _note in rows:
        if username in existing:
            continue
        db.add(models.Staff(username=username, password_hash=hash_password("123456"),
                            name=name, role=role))


def seed_categories(db):
    if db.query(models.Category).count() > 0:
        return
    for i, name in enumerate(CATEGORIES):
        db.add(models.Category(name=name, sort=i))


def cat_map(db):
    return {c.name: c.id for c in db.query(models.Category).all()}


def seed_dishes(db):
    cm = cat_map(db)
    for cat_name, name, price, has_photo, desc in DISHES:
        image = ("/web/uploads/dishes/%s.jpg" % PHOTO_INDEX[name]) if has_photo else ""
        existing = db.query(models.Dish).filter_by(name=name).first()
        if existing:
            # 幂等：老库补图片/简介（种子数据同步）
            if not existing.image:
                existing.image = image
            if not existing.description:
                existing.description = desc
            continue
        db.add(models.Dish(
            category_id=cm[cat_name], name=name, price=D(str(price)),
            description=desc, sales=0, image=image, emoji="",
            option_template=OPTION_TEMPLATES.get(name),
        ))


def dish_map(db):
    return {d.name: d.id for d in db.query(models.Dish).all()}


def seed_ingredients(db):
    if db.query(models.Ingredient).count() > 0:
        return
    rows = [
        # (名称, 单位, 库存, 安全线, 参考单价)
        ("猪五花肉", "g", 8000, 2000, 0.030),
        ("鸡胸肉", "g", 6000, 1500, 0.022),
        ("草鱼", "g", 10000, 2500, 0.026),
        ("嫩豆腐", "g", 12000, 3000, 0.008),
        ("五常大米", "g", 30000, 8000, 0.006),
        ("面粉", "g", 15000, 4000, 0.005),
        ("鸡蛋", "个", 120, 30, 0.80),
        ("番茄", "g", 8000, 2000, 0.006),
        ("黄瓜", "g", 6000, 1500, 0.005),
        ("大白菜", "g", 8000, 2000, 0.004),
        ("粉丝", "g", 5000, 1200, 0.012),
        ("花生米", "g", 3000, 800, 0.014),
        ("干辣椒", "g", 900, 800, 0.040),
        ("花椒", "g", 420, 500, 0.090),
        ("食用油", "g", 20000, 5000, 0.012),
        ("酸梅膏", "g", 4000, 1000, 0.030),
        ("王老吉", "瓶", 60, 20, 3.20),
        ("蒜苗", "g", 1500, 400, 0.010),
    ]
    rows += list(EXTRA_INGREDIENTS)  # 羊肉馆补充原料（羊肉/羊杂）
    for name, unit, stock, safe, price in rows:
        db.add(models.Ingredient(name=name, unit=unit, stock=D(str(stock)),
                                 safe_stock=D(str(safe)), ref_price=D(str(price))))


def ing_map(db):
    return {i.name: i.id for i in db.query(models.Ingredient).all()}


def seed_bom(db):
    if db.query(models.DishIngredient).count() > 0:
        return
    dm, im = dish_map(db), ing_map(db)
    for dish_name, items in BOM.items():
        for ing_name, qty, loss in items:
            db.add(models.DishIngredient(
                dish_id=dm[dish_name], ingredient_id=im[ing_name],
                quantity=D(str(qty)), loss_rate=D(str(loss)),
            ))


def seed_suppliers(db):
    if db.query(models.Supplier).count() > 0:
        return
    rows = [
        ("永辉食材配送", "张师傅", "13800001111"),
        ("老刘肉铺", "刘老板", "13900002222"),
        ("绿源蔬菜批发", "王姐", "13700003333"),
        ("川味调料商行", "陈经理", "13600004444"),
    ]
    for name, contact, phone in rows:
        db.add(models.Supplier(name=name, contact=contact, phone=phone))


def seed_tables(db):
    # 外带占位桌台(id=0)由 database.ensure_schema() 负责，这里只种实体桌台
    if db.query(models.DiningTable).filter(models.DiningTable.id != 0).count() > 0:
        return
    rows = []
    for i in range(1, 9):
        rows.append(("A%02d" % i, "大厅", 4))
    for i in range(1, 6):
        rows.append(("B%02d" % i, "大厅", 6))
    for i in range(1, 4):
        rows.append(("C%02d" % i, "包间", 8))
    for name, area, seats in rows:
        db.add(models.DiningTable(
            name=name, area=area, seats=seats, qr_scene=name,
            enabled=(name != "B03"),  # B03 停用
        ))


def main():
    Base.metadata.create_all(bind=engine)
    ensure_schema()  # 老库补新列（如 dish.emoji）
    db = SessionLocal()
    try:
        seed_shop(db)
        seed_staff(db)
        seed_categories(db)
        seed_dishes(db)
        seed_ingredients(db)
        seed_bom(db)
        seed_suppliers(db)
        seed_tables(db)
        db.commit()
        print("种子数据完成：")
        print("  员工:", db.query(models.Staff).count(), "（admin/cashier/waiter/kitchen，密码 123456）")
        print("  分类:", db.query(models.Category).count())
        print("  菜品:", db.query(models.Dish).count())
        print("  原料:", db.query(models.Ingredient).count())
        print("  BOM:", db.query(models.DishIngredient).count(), "条")
        print("  供应商:", db.query(models.Supplier).count())
        print("  桌台:", db.query(models.DiningTable).count(), "（B03 停用）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
