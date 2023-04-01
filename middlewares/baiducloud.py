import os
import json
import time
import aiohttp
from loguru import logger
from config import Config
from typing import Callable
from graia.ariadne.message.element import Image
from middlewares.middleware import Middleware

config = Config.load_config()


class BaiduCloud:
    def __init__(self):
        self.access_token = None
        self.expiration_time = None
        self.token_file = "/data/token_info.json"
        self.load_token_info()

    def save_token_info(self):
        try:
            with open(self.token_file, 'w') as f:
                json.dump({"access_token": self.access_token, "expiration_time": self.expiration_time}, f)
        except IOError as e:
            logger.error(f"无法保存Access_token到指定位置: {e}")

    def load_token_info(self):
        if not os.path.exists(self.token_file):
            os.makedirs(self.token_file)
        if os.path.exists(self.token_file):
            try:
                with open(self.token_file, 'r') as f:
                    token_info = json.load(f)
                    self.access_token = token_info.get("access_token")
                    self.expiration_time = token_info.get("expiration_time")
            except IOError as e:
                logger.error(f"无法从目标位置加载Access_token: {e}")
            except json.JSONDecodeError as e:
                logger.error(f"Access_token文件格式错误: {e}")

    async def get_access_token(self):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                    "https://aip.baidubce.com/oauth/2.0/token",
                    params={
                        "grant_type": "client_credentials",
                        "client_id": config.baiducloud.baidu_api_key,
                        "client_secret": config.baiducloud.baidu_secret_key,
                    }
            ) as response:
                response.raise_for_status()
                result = await response.json()
                self.access_token = result.get("access_token")
                expires_in = result.get("expires_in")

                # 计算 access_token 过期时间
                self.expiration_time = time.time() + expires_in - 60  # 提前 60 秒更新

                self.save_token_info()

                return self.access_token

    async def check_and_update_access_token(self):
        if not self.access_token or time.time() > self.expiration_time:
            await self.get_access_token()

    async def get_conclusion(self, text: str):
        baidu_url = f"https://aip.baidubce.com/rest/2.0/solution/v1/text_censor/v2/user_defined" \
                    f"?access_token={self.access_token}"
        headers = {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}

        async with aiohttp.ClientSession() as session:
            async with session.post(baidu_url, headers=headers, data={'text': text}) as response:
                response.raise_for_status()
                response_dict = await response.json()

        return response_dict


class MiddlewareBaiduCloud(Middleware):
    def __init__(self):
        self.baidu_cloud = BaiduCloud()

    async def handle_respond(self, session_id: str, prompt: str, rendered: str, respond: Callable, action: Callable):
        try:
            if config.baiducloud.check:
                if not self.baidu_cloud.access_token:
                    logger.debug(f"正在获取access_token，请稍等")
                    self.baidu_cloud.access_token = await self.baidu_cloud.get_access_token()

                # 不处理图片信息
                if isinstance(rendered, Image):
                    return await action(session_id, prompt, rendered, respond)

                response_dict = await self.baidu_cloud.get_conclusion(str(rendered))

                # 处理百度云审核结果
                conclusion = response_dict["conclusion"]
                if conclusion in "合规":
                    logger.success(f"百度云判定结果：{conclusion}")
                    return await action(session_id, prompt, rendered, respond)
                else:
                    msg = response_dict['data'][0]['msg']
                    logger.error(f"百度云判定结果：{conclusion}")
                    conclusion = f"{config.baiducloud.prompt_message}\n原因：{msg}"
                    return await action(session_id, prompt, conclusion, respond)
            # 未审核消息路径
            else:
                return await action(session_id, prompt, rendered, respond)
        except aiohttp.ClientError as e:
            logger.error(f"HTTP error occurred: {e}")
            conclusion = f"百度云判定出错\n以下是原消息：{rendered}"
            return await action(session_id, prompt, conclusion, respond)
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error occurred: {e}")
        except StopIteration as e:
            logger.error(f"StopIteration exception occurred: {e}")
