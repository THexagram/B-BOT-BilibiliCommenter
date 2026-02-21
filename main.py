
import logging
import re
import time
import requests
from typing import List, Optional, Dict

# 配置日志（支持UTF-8编码）
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bilibili_comment.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)


class BilibiliCommenter:
    def __init__(self, cookie: str, at_users: List[str], comment_template: str = "大家来看这个视频！@{}"):
        """初始化Bilibili评论器"""
        self.cookie = cookie
        self.at_users = at_users
        self.comment_template = comment_template
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Cookie": cookie,
            "Referer": "https://www.bilibili.com/",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        self.user_mid = self._get_user_mid()
        self.csrf_token = self._extract_csrf_token()

    def _extract_csrf_token(self) -> Optional[str]:
        """从Cookie中提取CSRF令牌（bili_jct）"""
        try:
            match = re.search(r'bili_jct=([^;]+)', self.cookie)
            return match.group(1) if match else None
        except Exception as e:
            logging.error(f"提取CSRF令牌失败: {str(e)}")
            return None

    def _get_user_mid(self) -> Optional[int]:
        """获取当前用户的mid"""
        try:
            response = requests.get("https://api.bilibili.com/x/web-interface/nav", headers=self.headers)
            data = response.json()
            return data["data"]["mid"] if data.get("code") == 0 else None
        except Exception as e:
            logging.error(f"获取用户ID失败: {str(e)}")
            return None

    def get_default_favorite_list(self) -> Optional[Dict]:
        """获取默认收藏夹信息"""
        if not self.user_mid:
            return None
        try:
            url = f"https://api.bilibili.com/x/v3/fav/folder/created/list-all?up_mid={self.user_mid}&jsonp=jsonp"
            response = requests.get(url, headers=self.headers)
            data = response.json()
            if data.get("code") == 0 and data["data"]["list"]:
                # 优先找标记为默认的收藏夹
                for folder in data["data"]["list"]:
                    if folder.get("is_default"):
                        return folder
                return data["data"]["list"][0]  # fallback到第一个
            return None
        except Exception as e:
            logging.error(f"获取收藏夹失败: {str(e)}")
            return None

    def get_favorite_videos(self, media_id: int, page: int = 1) -> Optional[Dict]:
        """获取收藏夹中的视频列表（分页）"""
        try:
            url = f"https://api.bilibili.com/x/v3/fav/resource/list?media_id={media_id}&pn={page}&ps=20&order=mtime&type=0&platform=web"
            response = requests.get(url, headers=self.headers)
            data = response.json()
            return data["data"] if data.get("code") == 0 else None
        except Exception as e:
            logging.error(f"获取视频列表失败: {str(e)}")
            return None

    def get_user_info(self, username: str) -> Optional[Dict]:
        """通过用户名获取用户信息"""
        try:
            url = f"https://api.bilibili.com/x/web-interface/search/type?keyword={username}&search_type=bili_user"
            response = requests.get(url, headers=self.headers)
            data = response.json()
            return data["data"]["result"][0] if data.get("code") == 0 and data["data"]["result"] else None
        except Exception as e:
            logging.error(f"获取用户信息失败: {str(e)}")
            return None

    def send_comment(self, oid: int, content: str) -> bool:
        """发送评论（带CSRF验证）"""
        if not self.csrf_token:
            logging.error("缺少CSRF令牌，无法评论")
            return False
        try:
            response = requests.post(
                "https://api.bilibili.com/x/v2/reply/add",
                headers=self.headers,
                data={
                    "oid": oid,
                    "type": 1,
                    "message": content,
                    "plat": 1,
                    "jsonp": "jsonp",
                    "csrf": self.csrf_token
                }
            )
            result = response.json()
            if result.get("code") == 0:
                logging.info(f"评论成功: 视频ID {oid}")
                return True
            logging.error(f"评论失败 (ID {oid}): {result.get('message')}")
            return False
        except Exception as e:
            logging.error(f"评论异常 (ID {oid}): {str(e)}")
            return False

    def process_favorite_videos(self, skip_count: int = 0, max_comment_count: Optional[int] = None) -> None:
        """
        处理默认收藏夹所有视频（修复分页逻辑）
        :param skip_count: 需要跳过的视频数量
        :param max_comment_count: 最多发送评论的视频数量（None表示评论所有符合条件的视频）
        """
        # 前置检查
        if not self.csrf_token:
            logging.error("CSRF令牌获取失败，退出")
            return

        # 获取默认收藏夹
        favorite = self.get_default_favorite_list()
        if not favorite:
            logging.error("未找到默认收藏夹，退出")
            return
        media_id = favorite["id"]
        logging.info(f"开始处理收藏夹: {favorite['title']} (ID: {media_id})，将跳过前{skip_count}个视频")
        # 新增：打印自定义评论数量配置
        if max_comment_count is not None:
            logging.info(f"本次配置最多评论{max_comment_count}个视频")

        # 构建@用户字符串
        at_users = []
        for username in self.at_users:
            user = self.get_user_info(username)
            if user:
                at_users.append(f"@{user['uname']}")
        if not at_users:
            logging.error("未找到有效用户，退出")
            return
        comment_content = self.comment_template.format(" ".join(at_users))
        logging.info(f"评论内容: {comment_content}")

        # 分页处理视频（修复分页逻辑）
        page = 1
        processed_count = 0  # 已处理（包括跳过）的视频总数
        total_videos = 0  # 总视频数（用于判断是否还有下一页）
        # 新增：已评论视频计数器
        commented_count = 0

        while True:
            video_data = self.get_favorite_videos(media_id, page)
            if not video_data or not video_data.get("medias"):
                break  # 无数据时退出循环

            # 更新总视频数（从API返回的page信息中获取）
            page_info = video_data.get("page", {})
            total_videos = page_info.get("count", 0)
            current_page_videos = video_data["medias"]
            logging.info(f"处理第{page}页，共{len(current_page_videos)}个视频（总视频数: {total_videos}）")

            for video in current_page_videos:
                processed_count += 1  # 计数+1（包括跳过的）

                # 判断是否需要跳过
                if processed_count <= skip_count:
                    logging.info(f"跳过视频 {processed_count}/{skip_count}: {video['title']} (ID: {video['id']})")
                    continue  # 跳过当前视频

                # 新增：判断是否达到最大评论数量，达到则停止
                if max_comment_count is not None and commented_count >= max_comment_count:
                    logging.info(f"已达到最大评论数量{max_comment_count}个，停止操作")
                    break

                # 处理需要评论的视频
                video_id = video["id"]
                logging.info(f"处理视频 {processed_count} (已跳过{skip_count}个): {video['title']} (ID: {video_id})")
                success = self.send_comment(video_id, comment_content)
                # 新增：评论成功才计数
                if success:
                    commented_count += 1
                time.sleep(15 if success else 8)  # 控制频率

            # 新增：判断是否达到最大评论数量，达到则停止分页
            if max_comment_count is not None and commented_count >= max_comment_count:
                break

            # 分页判断（关键修复：只要未达到总视频数，就继续加载下一页）
            if processed_count >= total_videos:
                break  # 所有视频已处理完毕
            page += 1  # 加载下一页

        # 新增：更新最终日志，包含已评论数量
        actual_commented = commented_count
        if max_comment_count is not None:
            actual_commented = min(commented_count, max_comment_count)
        logging.info(
            f"所有视频处理完毕，共处理{processed_count - skip_count}个视频（跳过{skip_count}个，总视频数{total_videos}，实际评论{actual_commented}个）")


if __name__ == "__main__":
    # 配置信息
    COOKIE = (
    "your cookie"
    )#这里填你的COOKIE
    AT_USERS = ["username","another username"]#这里写你想@的人用逗号隔开
    COMMENT_TEMPLATE = "尊敬的主，主……主人，这……这是我今天发……发……发现的有趣视频，请您有空……啊别打我……别打我喵呜~我会努力找更多视频的……别打我……@{}"#这里填你想评论的具体内容
    SKIP_COUNT = 20  # 需要跳过的视频数量
    # 新增：自定义最多评论数量配置（可修改为任意正整数，None表示评论所有）
    MAX_COMMENT_COUNT = 10

    # 执行程序（传入跳过数量和新增的最大评论数量参数）
    commenter = BilibiliCommenter(COOKIE, AT_USERS, COMMENT_TEMPLATE)
    commenter.process_favorite_videos(skip_count=SKIP_COUNT, max_comment_count=MAX_COMMENT_COUNT)  # 必须传入参数

