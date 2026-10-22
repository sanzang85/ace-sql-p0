from __future__ import annotations

import os
from typing import Dict

from .config import DATA_DICTIONARY_PATH, DB_SCHEMA_PATH
from .state import NL2SQLState


def load_data_dictionary(state: NL2SQLState) -> Dict:
    print("-> 执行：加载全局数据字典与业务规则 (数据表信息.md)")
    if not os.path.exists(DATA_DICTIONARY_PATH):
        print(f"警告：未找到文件 {DATA_DICTIONARY_PATH}")
        return {"data_dictionary": "未获取到数据字典信息。"}

    with open(DATA_DICTIONARY_PATH, "r", encoding="utf-8") as file:
        content = file.read()
    print("   [加载完毕] 成功载入数据字典及业务补充规则。")
    return {"data_dictionary": content}


def retrieve_db_knowledge(state: NL2SQLState) -> Dict:
    print("-> 执行：数据库知识检索 (直接加载完整 DDL 避免检索丢失)")
    if not os.path.exists(DB_SCHEMA_PATH):
        print(f"警告：未找到文件 {DB_SCHEMA_PATH}")
        return {"db_knowledge": "未找到数据库结构知识文件。", "full_db_schema": ""}

    with open(DB_SCHEMA_PATH, "r", encoding="utf-8") as file:
        sql_content = file.read()
    print("   [加载完毕] 成功加载完整数据库 Schema，供后续节点使用。")
    return {
        "db_knowledge": sql_content,
        "full_db_schema": sql_content,
    }

