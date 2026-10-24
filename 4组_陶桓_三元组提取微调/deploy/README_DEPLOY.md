# 云端部署说明（RTX 5090 32GB / AutoDL）

## 一、上传

本地把 `tourism_triple_deploy.tar.gz` 上传到实例数据盘：

```bash
scp -P <端口> tourism_triple_deploy.tar.gz root@<host>:/root/autodl-tmp/
```

云端解压：

```bash
cd /root/autodl-tmp && tar -xzf tourism_triple_deploy.tar.gz && cd tourism_triple_deploy
```

## 二、配置 DeepSeek Key（仅基线评估需要）

```bash
cp .env.example .env
vi .env        # 填 DEEPSEEK_API_KEY
```

## 三、一键运行

```bash
bash run_cloud.sh
```

默认：Qwen3-4B / 600 步 / bf16 LoRA（不量化）/ seq 1024 / batch 4。
预计 1.5 小时，费用 ≈ ¥4.5。

**网络选项（可选）：**

```bash
# ModelScope 下载用国内直连，默认不开代理
# 若需要拉 HuggingFace/GitHub，开启 AutoDL 学术加速：
USE_PROXY=1 bash run_cloud.sh

# pip 若走默认源慢，可指定镜像：
PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple bash run_cloud.sh
```

其他档位：

```bash
# 8B（QLoRA 4bit，显存更省）
BASE_MODEL=Qwen/Qwen3-8B USE_4BIT=1 BATCH=2 GRAD_ACCUM=8 bash run_cloud.sh

# 1.7B 复现本地结果
BASE_MODEL=Qwen/Qwen3-1.7B bash run_cloud.sh
```

## 四、产物

| 路径 | 内容 |
|---|---|
| `finetuned/<模型>_sft/` | LoRA adapter |
| `data/sft/responses_<模型>.jsonl` | 200 条 gold 预测 |
| `data/sft/eval_<模型>_vote.json` | 严格匹配指标 |
| 控制台输出 | 宽松匹配 P/R/F1 |

## 五、回传结果

```bash
scp -P <端口> -r root@<host>:/root/autodl-tmp/tourism_triple_deploy/finetuned/<模型>_sft ./finetuned/
scp -P <端口> root@<host>:/root/autodl-tmp/tourism_triple_deploy/data/sft/responses_<模型>.jsonl ./data/sft/
```

## 六、注意

- **5090 是 Blackwell（sm_120）**：镜像需 torch ≥ 2.7 + CUDA 12.8。`run_cloud.sh` 会先打印 torch 版本；若 CUDA 不可用，需换镜像或重装 torch。
- 模型与环境都放 `/root/autodl-tmp`（数据盘 50GB），系统盘只有 30GB。
- 训练脚本 `--no-4bit` 走 bf16 LoRA，已处理梯度检查点的梯度回传。
