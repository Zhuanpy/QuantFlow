import cv2


# 找图 返回最近似的点
def match_screenshot(img_path: str, template_path: str, use_gray=True):
    try:
        # 读取大图和小图
        target = cv2.imread(img_path)  # 要找的大图
        template = cv2.imread(template_path)  # 图中的小图

        if target is None or template is None:
            raise ValueError("无法读取输入的图像文件")

        # 可选的灰度转换
        if use_gray:
            target = cv2.cvtColor(target, cv2.COLOR_BGR2GRAY)
            template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

        # 模板匹配
        result = cv2.matchTemplate(target, template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

        # 返回匹配结果
        return min_val, max_val, min_loc, max_loc

    except Exception as e:
        print(f"匹配截图时出现错误: {e}")
        return None


if __name__ == '__main__':
    targets = 'targetfile/screenshot.jpg'
    templates = 'targetfile/loginsuccess.jpg'
    data = match_screenshot(targets, templates)
    print(data)
