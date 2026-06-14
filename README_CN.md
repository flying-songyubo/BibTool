# BibTeX 生成器 (BibTool)
🌐 语言: [English](README.md) | 中文

## 简介

本工具是一个基于 Python + Tkinter 的桌面应用程序，能够自动将论文参考文献列表（纯文本格式）转换为 BibTeX 条目。



它通过调用 CrossRef 和 OpenAlex 的公共 API，自动完成以下操作：



从参考文献字符串中提取标题、作者、年份、会议/期刊名等线索

智能匹配并获取完整元数据（包括 DOI、卷期页码、出版社等）

生成标准 BibTeX 格式，并自动处理重复条目

支持短标题、不完整信息的自动补全



适用场景：写论文、整理文献库时，将 Word 或 PDF 中的参考文献快速转为 BibTeX 格式



## 系统要求

操作系统：Windows / macOS / Linux（支持 Python 和 Tkinter）

Python 版本：3.6 或更高

网络连接：可访问 CrossRef 与 OpenAlex API



## 安装步骤

### 1. 安装 Python



如果电脑未安装 Python，请前往 https://www.python.org/downloads/ 下载安装。



安装时建议勾选 “Add Python to PATH”。



### 2. 下载项目代码



将 bibtool.py 保存到本地目录，例如：



C:\\BibTool\\bibtool.py



### 3. 安装依赖库



在终端执行：



pip install requests rapidfuzz



tkinter 为 Python 标准库，无需额外安装。



国内镜像源（可选）

pip install requests rapidfuzz -i https://pypi.tuna.tsinghua.edu.cn/simple

pip install requests rapidfuzz -i https://mirrors.aliyun.com/pypi/simple/

pip install requests rapidfuzz -i https://pypi.mirrors.ustc.edu.cn/simple/



## 使用方法

### 1. 启动程序



在 bibtool.py 所在目录运行：



python bibtool.py



启动后会打开图形界面窗口。



### 2. 准备参考文献文本



从论文或文档中复制参考文献列表。



格式要求：



每条文献以数字序号开头（如 1. 2. 3.）

每条一行

不要空行



示例：



1\. Zhang, L., \& Liu, Y. (2021). Deep learning for image recognition. IEEE Transactions on Pattern Analysis, 43(5), 1234-1245.

2\. Smith, J. (2020). A survey of cloud computing. Proceedings of the ACM Conference on Cloud Computing, 45-52.

3\. Wang, H. et al. (2019). Attention mechanisms in NLP. In Advances in Neural Information Processing Systems, 32, 1098-1109.



程序会自动提取标题、作者、年份等信息。



### 3. 运行查询



点击按钮：



运行查询，自动根据文献信息进行网络搜寻抓取论文元素



### 4. 查看结果

右侧区域：生成 BibTeX，可直接复制

左侧日志：显示匹配过程、相似度、API 返回结果

底部状态：进度与统计信息（成功 / 失败 / 重复）



### 5. 保存 BibTeX



程序不会自动保存文件。



请手动复制右侧结果到 .bib 文件中保存。



## 示例

### 输入

1\. Goodfellow, I., Pouget-Abadie, J., Mirza, M., Xu, B., Warde-Farley, D., Ozair, S., ... \& Bengio, Y. (2014). Generative adversarial nets. Advances in neural information processing systems, 27.



### 输出

@article{Goodfellow2014Generative,

&#x20; author = {Goodfellow, Ian and Pouget-Abadie, Jean and Mirza, Mehdi and Xu, Bing and Warde-Farley, David and Ozair, Sherjil and Courville, Aaron and Bengio, Yoshua},

&#x20; title = {Generative Adversarial Nets},

&#x20; journal = {Advances in Neural Information Processing Systems},

&#x20; volume = {27},

&#x20; year = {2014},

&#x20; doi = {10.5555/2969033.2969125}

}



实际输出取决于 API 返回结果。



## 注意事项

单条文献处理时间约 2–3 秒

网络或 API 限流时会自动重试

相似度过低的条目会自动跳过

程序内置缓存机制，可加速重复查询

若需重置，重新粘贴文本即可



## 常见问题



Q: No module named 'requests'

A: 执行 pip install requests rapidfuzz



Q: 中文乱码或无法启动

A: Windows 可执行 chcp 65001



Q: 查询无响应

A: 检查网络或 API 限流情况



Q: author 格式不对

A: 已自动转换为 BibTeX 标准 Last, First and ...



Q: 为什么匹配不到文献

A: 通常由于标题过短或信息不足，可补充作者或年份



## 依赖库说明

库名	作用

requests	调用 CrossRef / OpenAlex API

rapidfuzz	文本相似度计算

tkinter	GUI（Python 内置）

re / time / threading / queue	标准库



## 高级选项



可在 bibtool.py 中修改：



CROSSREF\_ROWS = 12

MIN\_MATCH\_SCORE = 60

MIN\_TITLE\_SIMILARITY = 60

FINAL\_SIMILARITY\_THRESHOLD = 65



修改后重新运行即可生效。



## 许可证



本工具仅供个人学术用途，不得用于商业用途。



CrossRef 与 OpenAlex API 使用需遵守其服务条款。

