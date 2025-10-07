import re
import sys
import json
import logging
import requests
import warnings
from datetime import datetime, date
from pathlib import Path

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('fan_control.log')
    ]
)

# 加载配置文件
def load_config():
    config_path = Path('fanSpeed.json')
    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
                # 确保返回的是配置数组
                if isinstance(config_data, list):
                    return config_data
                else:
                    # 如果是单个配置，转换为数组
                    return [config_data]
        except Exception as e:
            logging.error(f"加载配置文件失败：{e}")
    
    # 当配置文件不存在或加载失败时返回空数组
    return []

class FanController:
    def __init__(self, config):
        self.host = config['bmc_host']
        self.username = config['username']
        self.password = config['password']
        self.fans_count = config['fans_count']
        self.session = requests.Session()
        self.session.verify = False
        self.headers = {
            "content-type": "application/json",
            "User-Agent": r"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36 Edg/117.0.2045.60",
        }
        warnings.filterwarnings("ignore")

    def get_random(self):
        try:
            url = f"https://{self.host}/api/randomtag"
            res = self.session.get(url, headers=self.headers)
            res.raise_for_status()
            return res.json()["random"]
        except Exception as e:
            logging.error(f"获取随机标签失败：{e}")
            raise

    def login(self):
        try:
            random_string = self.get_random()
            url = f"https://{self.host}/api/session"
            self.headers["content-type"] = "application/x-www-form-urlencoded; charset=UTF-8"
            
            data = {
                "encrypt_flag": 0,
                "username": self.username,
                "password": self.password,
                "login_tag": str(random_string)
            }
            
            response = self.session.post(url, headers=self.headers, data=data)
            response.raise_for_status()
            
            if "Set-Cookie" not in response.headers:
                raise ValueError("响应中没有Set-Cookie头")
            
            cookies = response.headers["Set-Cookie"].split(';')
            session_id = None
            
            for cookie in cookies:
                cookie = cookie.strip()
                if cookie.startswith(("SESSION=", "QSESSIONID=")):
                    session_id = cookie.split('=')[1]
                    break
            
            if not session_id:
                raise ValueError("无法获取会话ID")
            
            response_json = response.json()
            if "CSRFToken" not in response_json:
                raise ValueError("无法获取CSRFToken")
            
            self.headers["X-Csrftoken"] = response_json["CSRFToken"]
            self.headers["Cookie"] = f"lang=zh-cn;QSESSIONID={session_id}; refresh_disable=1"
            self.headers["content-type"] = "application/json"
            
            logging.info("登录成功")
            
        except Exception as e:
            logging.error(f"登录失败：{e}")
            raise

    def set_fan_mode(self, mode="manual"):
        try:
            url = f"https://{self.host}/api/settings/fans-mode"
            data = {"control_mode": mode}
            response = self.session.put(url, headers=self.headers, json=data)
            response.raise_for_status()
            logging.info(f"设置风扇模式为：{mode}")
        except Exception as e:
            logging.error(f"设置风扇模式失败：{e}")
            raise

    def set_fan_speed(self, speed):
        success_count = 0
        for i in range(self.fans_count):
            try:
                url = f'https://{self.host}/api/settings/fan/{i}'
                data = {"duty": speed}
                response = self.session.put(url=url, json=data, headers=self.headers)
                response.raise_for_status()
                response_data = response.json()
                logging.info(f"风扇 {i} 转速已设置为 {response_data['duty']}%")
                success_count += 1
            except Exception as e:
                logging.error(f"设置风扇 {i} 转速失败：{e}")
        
        return success_count

    def get_fan_status(self):
        try:
            url = f"https://{self.host}/api/status/fan_info"
            response = self.session.get(url, headers=self.headers)
            response.raise_for_status()
            fan_info = response.json()
            
            if 'fans' in fan_info:
                logging.info("\n当前风扇状态：")
                for fan in fan_info['fans']:
                    logging.info(json.dumps(fan, indent=2, ensure_ascii=False))
            else:
                logging.warning("未获取到风扇信息")
                
        except Exception as e:
            logging.error(f"获取风扇状态失败：{e}")
            raise

def get_fan_speed_input():
    """获取用户输入的风扇转速或模式"""
    if len(sys.argv) > 1:
        if sys.argv[1].lower() == 'auto':
            return 'auto'
        try:
            speed = int(sys.argv[1])
            if 0 <= speed <= 100:
                return speed
            logging.error("风扇转速必须在0到100之间")
        except ValueError:
            logging.error("请输入有效的数字或 'auto'")
        sys.exit(1)
    
    # 未指定参数时返回None，表示需要自动判断执行模式
    return None

def is_weekend():
    """判断当前是否为周末"""
    today = datetime.now().weekday()
    # 周六(5)或周日(6)为周末
    return today >= 5

def is_chinese_holiday():
    """判断当前是否为中国国定节假日或周末（根据API信息）"""
    today = date.today()
    
    try:
        # 使用免费的节假日API获取最新数据
        # 格式化日期为YYYYMMDD格式
        date_str = today.strftime('%Y%m%d')
        # 发送请求到API
        response = requests.get(f'https://tool.bitefu.net/jiari/?d={date_str}&info=1', timeout=5)
        response.raise_for_status()  # 如果状态码不是200，抛出异常
        
        # 解析响应数据
        data = response.json()
        
        # 检查是否为节假日或周末
        # API返回的type: 0=工作日, 1=周末, 2=节假日
        if data.get('status') == 1 and (data.get('type') == 2 or data.get('type') == 1):
            if data.get('type') == 2:
                logging.info(f"通过API确认 {today} 是中国节假日")
            else:
                logging.info(f"通过API确认 {today} 是周末")
            return True
        else:
            logging.info(f"通过API确认 {today} 不是中国节假日或周末")
            return False
    except Exception as e:
        logging.warning(f"获取节假日API数据失败: {str(e)}，使用本地节假日列表")
        
        # 如果API调用失败，使用本地的硬编码节假日列表作为备用
        # 节假日列表 (月, 日)
        holidays = [
            (1, 1),  # 元旦
            (1, 2),  # 元旦假期
            (1, 3),  # 元旦假期
            (4, 4),  # 清明节
            (4, 5),  # 清明节
            (5, 1),  # 劳动节
            (5, 2),  # 劳动节
            (5, 3),  # 劳动节
            (10, 1), # 国庆节
            (10, 2), # 国庆节
            (10, 3), # 国庆节
            (10, 4), # 国庆节
            (10, 5)  # 国庆节
        ]
        
        # 检查今天是否在节假日列表中
        is_holiday = (today.month, today.day) in holidays
        if is_holiday:
            logging.info(f"通过本地列表确认 {today} 是中国节假日")
        else:
            logging.info(f"通过本地列表确认 {today} 不是中国节假日")
        
        return is_holiday

def main():
    try:
        # 首先检查节假日状态（显示联网更新是否成功）
        logging.info("正在检查节假日状态...")
        # 调用is_chinese_holiday()函数获取最新的节假日数据
        is_holiday = is_chinese_holiday()
        
        # 加载配置数组
        configs = load_config()
        
        # 获取目标转速或模式
        target = get_fan_speed_input()
        
        # 如果未指定转速，根据日期自动选择执行模式
        if target is None:
            # 判断当前是否为周末或中国节假日
            # 注意：is_chinese_holiday()函数在API返回type=1（周末）时也会返回False，
            # 但API已经确认是周末，所以我们需要额外检查API返回的周末信息
            if is_weekend() or is_holiday:
                target = 'auto'
                logging.info(f"当前是周末或中国节假日，自动执行auto模式")
            else:
                # 使用配置中的fan_speed值
                if configs and 'fan_speed' in configs[0]:
                    target = configs[0]['fan_speed']
                    logging.info(f"使用配置文件中的转速设置：{target}%")
                else:
                    logging.error("未指定转速且配置文件中没有fan_speed字段")
                    sys.exit(1)
        
        # 顺序执行每个配置
        for i, config in enumerate(configs):
            description = config.get('description', '未命名服务器')
            logging.info(f"\n===== 执行配置 {i+1}/{len(configs)} - {description} =====")
            
            try:
                # 创建控制器实例
                controller = FanController(config)
                
                # 执行控制流程
                controller.login()
                
                # 根据目标类型执行不同操作
                if target == 'auto':
                    controller.set_fan_mode("auto")
                    logging.info(f"服务器 {config['bmc_host']} 的风扇已设置为自动模式")
                else:
                    # 如果当前配置有独立的fan_speed，则使用它
                    if 'fan_speed' in config:
                        current_speed = config['fan_speed']
                    else:
                        current_speed = target
                    
                    controller.set_fan_mode("manual")
                    success_count = controller.set_fan_speed(current_speed)
                    
                    # 输出执行结果
                    if success_count == config['fans_count']:
                        logging.info(f"服务器 {config['bmc_host']} 所有风扇({success_count}/{config['fans_count']})转速设置成功")
                    else:
                        logging.warning(f"服务器 {config['bmc_host']} 部分风扇设置失败，成功率：{success_count}/{config['fans_count']}")
                
                # 获取当前状态
                controller.get_fan_status()
                
            except Exception as e:
                logging.error(f"配置 {i+1} 执行失败：{e}")
                # 继续执行下一个配置
                continue
        
        logging.info("\n所有配置执行完毕")
        
    except Exception as e:
        logging.error(f"程序执行出错：{e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
