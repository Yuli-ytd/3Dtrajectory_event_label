FROM python:3.11-slim

ARG DEBIAN_FRONTEND=noninteractive
ARG USERNAME=nol
ARG USER_UID=1000
ARG USER_GID=$USER_UID

ENV TZ=Asia/Taipei
ENV PIP_DEFAULT_TIMEOUT=100

SHELL ["/bin/bash", "-c"]

# 安裝最小 X11 Library + 清理 apt 快取 + pip 安裝 Python 套件
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libx11-6 \
    libxext6 \
    libxcb1 \
    libice6 \
    libsm6 \
    libgl1-mesa-glx \
    python3-pyqt5 \
    sudo \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir -r ./requirements.txt

# Create the user
RUN groupadd --gid $USER_GID $USERNAME \
&& useradd --uid $USER_UID --gid $USER_GID --groups sudo,video -m -s /bin/bash $USERNAME \
&& echo $USERNAME ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/$USERNAME \
&& chmod 0440 /etc/sudoers.d/$USERNAME

USER ${USERNAME}

# 設定工作目錄
WORKDIR /app

# 預設啟動 bash（可改成你的執行指令）
CMD ["bash"]