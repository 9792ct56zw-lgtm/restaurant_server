# -*- coding: utf-8 -*-
"""导入真实菜单：把现有库切换为羊肉馆真实菜单（幂等，可重复执行）。

- 分类：招牌菜→本店特色、汤品→汤类 自动改名，缺失分类补齐
- 菜品：按真实菜单 upsert（名称匹配则更新价格/分类/图片，重新上架）
- 旧演示菜品：全部下架（status=off，保留历史订单引用，菜单不再显示）
- 原料：补加羊肉/羊杂；BOM：为真实菜单菜品补齐配方（已有配方不动）
- 图片：web/uploads/dishes/ 下已裁剪好的菜品照片（无照片的菜留空）
"""
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

from app.database import Base, engine, SessionLocal, ensure_schema  # noqa: E402
from app import models  # noqa: E402
from menu_data import CATEGORIES, DISHES, PHOTO_INDEX, OPTION_TEMPLATES, EXTRA_INGREDIENTS, BOM  # noqa: E402

D = Decimal

# 旧分类名 -> 新分类名
LEGACY_CATEGORY_RENAME = {"招牌菜": "本店特色", "汤品": "汤类"}


def main():
    Base.metadata.create_all(bind=engine)
    ensure_schema()
    db = SessionLocal()
    try:
        # ---------- 1. 分类 ----------
        for legacy, new in LEGACY_CATEGORY_RENAME.items():
            old = db.query(models.Category).filter_by(name=legacy).first()
            has_new = db.query(models.Category).filter_by(name=new).first()
            if old and not has_new:
                old.name = new
        for i, name in enumerate(CATEGORIES):
            cat = db.query(models.Category).filter_by(name=name).first()
            if cat is None:
                cat = models.Category(name=name, sort=i)
                db.add(cat)
                print("新增分类：", name)
            elif cat.sort != i:
                cat.sort = i
        db.flush()
        cat_map = {c.name: c.id for c in db.query(models.Category).all()}

        # ---------- 2. 菜品 upsert ----------
        menu_names = set()
        for cat_name, name, price, has_photo, desc in DISHES:
            menu_names.add(name)
            dish = db.query(models.Dish).filter_by(name=name).first()
            image = ("/web/uploads/dishes/%s.jpg" % PHOTO_INDEX[name]) if has_photo else ""
            if dish is None:
                db.add(models.Dish(
                    category_id=cat_map[cat_name], name=name, price=D(str(price)),
                    description=desc, sales=0, image=image, emoji="",
                    option_template=OPTION_TEMPLATES.get(name), status=models.DISH_ON,
                ))
            else:
                dish.category_id = cat_map[cat_name]
                dish.price = D(str(price))
                dish.description = desc or dish.description
                dish.image = image
                dish.emoji = ""
                dish.status = models.DISH_ON
                dish.option_template = OPTION_TEMPLATES.get(name) or dish.option_template
        db.flush()

        # ---------- 3. 旧演示菜品下架 ----------
        off_count = 0
        for dish in db.query(models.Dish).filter(
                models.Dish.status == models.DISH_ON,
                ~models.Dish.name.in_(menu_names)).all():
            dish.status = models.DISH_OFF
            off_count += 1

        # ---------- 4. 原料与 BOM ----------
        ing_map = {i.name: i.id for i in db.query(models.Ingredient).all()}
        for name, unit, stock, safe, price in EXTRA_INGREDIENTS:
            if name not in ing_map:
                ing = models.Ingredient(name=name, unit=unit, stock=D(str(stock)),
                                        safe_stock=D(str(safe)), ref_price=D(str(price)))
                db.add(ing)
                db.flush()
                ing_map[name] = ing.id
                print("新增原料：", name)
        dish_map = {d.name: d.id for d in db.query(models.Dish).all()}
        # BOM 引用的原料缺失时自动补齐（老库原料清单与本菜单不完全一致）
        for dish_name, items in BOM.items():
            for ing_name, _qty, _loss in items:
                if ing_name not in ing_map:
                    ing = models.Ingredient(name=ing_name, unit="g", stock=D("10000"),
                                            safe_stock=D("1000"), ref_price=D("0.01"))
                    db.add(ing)
                    db.flush()
                    ing_map[ing_name] = ing.id
                    print("新增原料：", ing_name)
        bom_added = 0
        for dish_name, items in BOM.items():
            did = dish_map.get(dish_name)
            if not did:
                continue
            if db.query(models.DishIngredient).filter_by(dish_id=did).count() > 0:
                continue  # 已有配方不动
            for ing_name, qty, loss in items:
                db.add(models.DishIngredient(
                    dish_id=did, ingredient_id=ing_map[ing_name],
                    quantity=D(str(qty)), loss_rate=D(str(loss)),
                ))
                bom_added += 1

        db.commit()
        print("菜单导入完成：")
        print("  分类:", db.query(models.Category).count(), "（含", "、".join(CATEGORIES), "）")
        print("  上架菜品:", db.query(models.Dish).filter_by(status=models.DISH_ON).count())
        print("  下架旧菜:", off_count, "道（历史订单仍可查看）")
        print("  带照片菜品:", db.query(models.Dish).filter(
            models.Dish.image != "", models.Dish.status == models.DISH_ON).count())
        print("  原料:", db.query(models.Ingredient).count())
        print("  BOM 新增:", bom_added, "条")
    finally:
        db.close()


if __name__ == "__main__":
    main()
