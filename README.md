# dedup — 内容级重复文件清理工具

找出多个文件夹中内容相同的重复文件，将低优先级的副本移动到 Dedup 目录。

## 核心设计

### 判重策略：三级递进

| 阶段 | 操作 | 说明 |
|------|------|------|
| ① 文件大小 | 按字节数分组 | 大小不同的文件绝不可能内容相同，直接排除 |
| ② SHA256 | 同大小组内全量哈希 | 只有 ≥2 个文件共享同一个大小时才触发哈希 |
| ③ 哈希分组 | 相同哈希且 ≥2 个条目 → 重复组 | 精准，零误判 |

大部分文件在阶段①就被过滤掉，大文件不会做无意义的哈希计算。

### 保留/移动优先级

命令行参数顺序决定优先级：**第一个文件夹优先级最高。**

```
python3 dedup.py A/ B/ C/
```

- **跨文件夹**：A > B > C，后面的优先被移走
- **同文件夹内**：按相对路径字典序排序，小的留、大的移

示例：

```
# A/stuff/config.json 和 A/stuff/config_z.json 内容相同
# 都在 A 内，config.json < config_z.json（字典序）
KEEP  A/stuff/config.json
MOVE  A/stuff/config_z.json → Dedup/A/stuff/config_z.json
```

### Dedup 目录结构

移走的文件保留完整来源信息：

```
Dedup/<源文件夹名>/<相对路径>
```

例：`B/docs/readme.txt` → `Dedup/B/docs/readme.txt`

### 安全红线

| 场景 | 行为 |
|------|------|
| 符号链接 | 跳过，不参与判重 |
| Dedup 在源文件夹内 | 扫描时自动排除 Dedup 子目录，避免循环 |
| Dedup 目标路径已存在 | 自动加 `_1`、`_2` 后缀 |
| 文件无法读取/移动 | 跳过并警告，继续处理其余文件 |
| 移动失败 | 非零退出码 |

## 使用方法

```bash
# 预览（不移动任何文件）
python3 dedup.py --dry-run A/ B/ C/

# 执行移动
python3 dedup.py A/ B/ C/

# 指定 Dedup 目录
python3 dedup.py -d ./我的重复文件 A/ B/ C/

# 帮助
python3 dedup.py --help
```

## 输出示例

```
Sources: /data/A, /data/B, /data/C
Dedup:   /data/Dedup
Mode:    DRY RUN

Scanning... 1,234 files found (3 skipped)
Hashing files in same-size groups... 5 duplicate groups (12 files to move)

[Group] SHA256: a1b2c3... (size: 12,345 bytes)
  KEEP  /data/A/docs/readme.txt
  MOVE  /data/B/docs/readme.txt → /data/Dedup/B/docs/readme.txt
  MOVE  /data/C/backup/readme.txt → /data/Dedup/C/backup/readme.txt

[Group] SHA256: d4e5f6... (size: 5,678 bytes)
  KEEP  /data/A/stuff/config.json
  MOVE  /data/B/stuff/config.json → /data/Dedup/B/stuff/config.json

=== Summary ===
  Total files scanned:   1,234
  Skipped (symlinks, etc): 3
  Duplicate groups:      5
  Files moved:           12
  Files failed:          0
  Space saved (approx):  147,456 bytes
```

## 依赖

- Python 3.8+（标准库 only，无需 pip install）
