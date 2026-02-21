import datetime
import os
import requests
from bs4 import BeautifulSoup
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed


def scrape_fumen_database():
    """
    爬取 fumen-database.com 的难度列表页面，
    提取所有歌曲的链接
    """
    url = "https://fumen-database.com/difficulty/?const_desc"

    # 设置请求头，模拟浏览器访问
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        response.encoding = "utf-8"

        # 使用 BeautifulSoup 解析 HTML
        soup = BeautifulSoup(response.text, "html.parser")

        # 查找目标容器
        top_wrapper = soup.find("div", class_="top-wrapper")

        if not top_wrapper:
            return []

        table_song_data_wrapper = top_wrapper.find(
            "div", class_="table_song_data-wrapper"
        )

        if not table_song_data_wrapper:
            return []

        table_song_data = table_song_data_wrapper.find("div", class_="table_song_data")

        if not table_song_data:
            return []

        # 提取所有包含 table_song_name 的链接
        song_links = []
        song_name_divs = table_song_data.find_all(
            "div", class_=lambda x: bool(x and "table_song_name" in x)
        )

        for div in song_name_divs:
            a_tag = div.find("a")
            if a_tag and a_tag.get("href"):
                href: str = a_tag.get("href")  # pyright: ignore[reportAssignmentType]
                # 如果是相对路径，转换为完整 URL
                if href and href.startswith("/"):
                    href = f"https://fumen-database.com{href}"

                song_info = {"href": href, "title": a_tag.get_text(strip=True)}
                song_links.append(song_info)

        return song_links

    except Exception:
        return []


def extract_song_id_from_url(url):
    """从 URL 中提取歌曲 ID (xxx-x 部分)"""
    match = re.search(r"/song/([^/]+)/", url)
    if match:
        return match.group(1)
    return None


def parse_script_data(soup):
    """解析页面中的 script nonce 数据"""
    script_tags = soup.find_all("script", {"nonce": True})

    for script in script_tags:
        script_content = script.string
        if script_content and "song_name" in script_content:
            # 尝试提取 JavaScript 对象数据
            try:
                # 查找 const xxx = {...}; 或 var xxx = {...};
                match = re.search(
                    r"(?:const|var|let)\s+(\w+)\s*=\s*\{([^}]+)\};",
                    script_content,
                    re.DOTALL,
                )
                if match:
                    obj_content = match.group(2)

                    # 将 JavaScript 对象格式转换为 JSON 格式
                    # 1. 将单引号替换为双引号（处理转义的单引号）
                    json_content = obj_content

                    # 先处理值中的单引号（转义它们）
                    # 匹配 key: 'value' 模式
                    def replace_quotes(match):
                        key = match.group(1)
                        value = match.group(2)
                        # 转义值中的双引号和反斜杠
                        value = value.replace("\\", "\\\\").replace('"', '\\"')
                        return f'"{key}": "{value}"'

                    # 匹配格式: key: 'value'
                    json_content = re.sub(
                        r"(\w+)\s*:\s*'([^']*)'", replace_quotes, json_content
                    )

                    # 匹配格式: key: "value"（如果已经是双引号）
                    json_content = re.sub(
                        r'(\w+)\s*:\s*"([^"]*)"', r'"\1": "\2"', json_content
                    )

                    # 包装成完整的 JSON 对象
                    json_str = "{" + json_content + "}"

                    # 使用 json.loads 解析
                    data = json.loads(json_str)
                    data = {k: v for k, v in data.items() if k != "song_name"}

                    # 尝试将数字字符串转换为数字
                    for key, value in data.items():
                        if isinstance(value, str):
                            try:
                                data[key] = float(value)
                            except ValueError:
                                pass  # 保持字符串

                    if data:
                        return data
            except Exception:
                continue

    return None


def scrape_song_detail(url, error_log=None):
    """
    爬取单个歌曲详情页
    提取 constant, totalNotes 和雷达图数据
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        response.encoding = "utf-8"

        soup = BeautifulSoup(response.text, "html.parser")

        # 提取 song_id
        song_id = extract_song_id_from_url(url)
        if not song_id:
            if error_log is not None:
                error_log.append(f"无法从 URL 提取歌曲 ID: {url}")
            return None

        # 解析 script nonce 中的数据
        radar_data = parse_script_data(soup)
        if not radar_data:
            if error_log is not None:
                error_log.append(f"[{song_id}] 未找到 script 数据")
            return None

        # 提取 constant
        constant = None
        top_wrapper = soup.find("div", class_="top-wrapper")
        if top_wrapper:
            const_area = top_wrapper.find("div", class_="const_area")
            if const_area:
                p_tags = const_area.find_all("p")
                for p in p_tags:
                    span = p.find("span", id="score_const_origin")
                    if span:
                        const_text = span.get_text(strip=True)
                        try:
                            constant = float(const_text)
                        except ValueError:
                            if error_log is not None:
                                error_log.append(
                                    f"[{song_id}] 无法解析 constant: {const_text}"
                                )
                        break

        # 提取 totalNotes
        total_notes = None
        song_info_div = soup.find("div", class_="song_info")
        if song_info_div:
            song_info_area = song_info_div.find("div", class_="song_info_area")
            if song_info_area:
                divs = song_info_area.find_all("div")
                for div in divs:
                    # 查找包含 title_combo 的 img
                    img = div.find("img", src=lambda x: x and "title_combo" in x)  # pyright: ignore[reportArgumentType]
                    if img:
                        # 找到这个 div 中的 p 标签
                        p_tag = div.find("p")
                        if p_tag:
                            notes_text = p_tag.get_text(strip=True)
                            try:
                                total_notes = int(notes_text)
                            except ValueError:
                                if error_log is not None:
                                    error_log.append(
                                        f"[{song_id}] 无法解析 totalNotes: {notes_text}"
                                    )
                        break

        # 构建结果
        result = {
            "constant": constant,
            "totalNotes": total_notes,
            "composite": radar_data.get("radar_compound", None),
            "avgDensity": radar_data.get("radar_density_ave", None),
            "instDensity": radar_data.get("radar_density_inst", None),
            "separation": radar_data.get("radar_division", None),
            "bpmChange": radar_data.get("radar_change_bpm", None),
            "hsChange": radar_data.get("radar_change_hs", None),
        }

        return song_id, result

    except requests.exceptions.RequestException as e:
        if error_log is not None:
            error_log.append(f"请求错误 ({url}): {e}")
        return None
    except Exception as e:
        if error_log is not None:
            error_log.append(f"解析错误 ({url}): {e}")
        return None


def save_to_json(data, filename: str, indent: int | None = None):
    """将数据保存为 JSON 文件"""
    if os.path.exists(filename):
        os.remove(filename)

    if os.path.dirname(filename) != "" and not os.path.exists(
        os.path.dirname(filename)
    ):
        os.makedirs(os.path.dirname(filename))

    with open(filename, "x", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)

    print(f"\n数据已保存到 {filename}")


difficulty_map = {"4": "oni", "5": "ura"}


def convert_to_new_format(old_data, id_to_title_map):
    """
    将旧格式数据转换为新格式
    旧格式: {"id-difficulty": {song_data}}
    新格式: [{id, title, constants: {4: {...}, 5: {...}}}]
    """
    songs_map = {}

    for key, song_data in old_data.items():
        # 解析 key: "id-difficulty"
        last_dash_index = key.rfind("-")
        if last_dash_index == -1:
            print(f"警告: 跳过格式不正确的键: {key}")
            continue

        song_id = key[:last_dash_index]
        difficulty = key[last_dash_index + 1 :]

        # 验证 difficulty 是 4 或 5
        if difficulty not in ["4", "5"]:
            print(f"警告: 跳过不支持的难度: {key} (difficulty: {difficulty})")
            continue

        # 如果这个 id 还没有记录，创建新条目
        if song_id not in songs_map:
            songs_map[song_id] = {
                "id": int(song_id),
                "title": id_to_title_map.get(song_id, ""),
                "constants": {},
            }

        # 添加难度数据
        songs_map[song_id]["constants"][difficulty_map[difficulty]] = song_data

    # 转换为数组并按 id 排序
    new_data = sorted(songs_map.values(), key=lambda x: x["id"])

    for song in new_data:
        song["constants"] = dict(sorted(song["constants"].items()))

    return new_data


def scrape_single_song(index, total, song_link, max_retries=5, error_log=None):
    """
    爬取单个歌曲，包含重试机制
    返回: (song_id, song_data) 或 (None, song_title) 表示失败
    """
    result = None
    retry_count = 0

    # 重试机制：最多尝试max_retries次
    while retry_count < max_retries and result is None:
        result = scrape_song_detail(song_link["href"], error_log)
        retry_count += 1

    if result:
        return (result, song_link["title"])
    else:
        if error_log is not None:
            error_log.append(
                f"[{song_link['title']}] 爬取失败（已重试 {max_retries} 次）"
            )
        return (None, song_link["title"])  # 返回失败信息


def scrape_all_songs(max_workers=4):
    """爬取所有歌曲（使用多线程池加速）"""

    # 第一步：获取所有歌曲链接
    song_links = scrape_fumen_database()

    if not song_links:
        print("✗ 未找到任何歌曲链接")
        return None

    print(f"找到 {len(song_links)} 个歌曲，使用 {max_workers} 个线程进行并发爬取...")

    all_songs_data = {}
    success_count = 0
    fail_count = 0
    fails = []
    errors = []
    id_to_title_map = {}
    max_retries = 5
    total = len(song_links)

    # 使用ThreadPoolExecutor进行并发爬取
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        futures = {
            executor.submit(
                scrape_single_song, i + 1, total, song_link, max_retries, errors
            ): song_link
            for i, song_link in enumerate(song_links)
        }

        # 处理完成的任务
        for future in as_completed(futures):
            try:
                result = future.result()
                if result[0] is not None:  # 爬取成功
                    song_id, song_data = result[0]
                    song_name = result[1]
                    all_songs_data[song_id] = song_data
                    last_dash_index = song_id.rfind("-")
                    song_id = song_id[:last_dash_index]
                    id_to_title_map[song_id] = song_name
                    success_count += 1
                else:  # 爬取失败
                    fail_count += 1
                    fails.append(result[1])
            except Exception as e:
                errors.append(f"线程执行错误: {e}")
                fail_count += 1

    # 转换为新格式
    new_format_data = convert_to_new_format(all_songs_data, id_to_title_map)

    # 统一输出结果
    print("\n" + "=" * 60)
    print("爬取完成！")
    print(f"成功: {success_count} 个")
    print(f"失败: {fail_count} 个")
    print(f"转换: {len(new_format_data)} 首歌曲")

    if errors or fails:
        print("\n" + "=" * 60)
        print("错误和失败信息：")
        if errors:
            print("\n【执行错误】")
            for error in errors:
                print(f"  • {error}")
        if fails:
            print("\n【爬取失败的歌曲】")
            for fail in fails:
                print(f"  • {fail}")

    if errors:
        print("\n【详细日志】")
        for error in errors:
            print(f"  • {error}")

    print("=" * 60)

    return new_format_data


def test_single_page(url):
    """测试单个页面的爬取"""
    print(f"测试单个页面: {url}")
    print("=" * 50)

    result = scrape_song_detail(url)

    if result[0]:
        song_id, song_data = result[0]
        title = result[1]
        print("\n" + "=" * 50)
        print("爬取成功！")
        print("\n歌曲 ID:", song_id)
        print("\n歌曲标题:", title)
        print("\n歌曲数据 (旧格式):")
        print(json.dumps({song_id: song_data}, ensure_ascii=False, indent=2))

        # 转换为新格式
        old_format = {song_id: song_data}
        new_format = convert_to_new_format(old_format, {song_id: title})

        print("\n歌曲数据 (新格式):")
        print(json.dumps(new_format, ensure_ascii=False, indent=2))

        return new_format
    else:
        print("\n" + "=" * 50)
        print("爬取失败！")
        return None


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        test_url = r"https://fumen-database.com/song/714-4/"
        result = test_single_page(test_url)
        if result:
            save_to_json(result, "test_song_data.json")
    else:
        all_songs_data = scrape_all_songs()
        if all_songs_data:
            save_to_json(all_songs_data, "songs.json")
            save_to_json(all_songs_data, "songs_raw.json", 2)
            save_to_json(
                all_songs_data,
                f"history/{datetime.datetime.now().strftime('%Y-%m-%d')}.json",
            )
