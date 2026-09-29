---
license: mit
language:
- zh
tags:
  - sentence-transformers
  - feature-extraction
  - sentence-similarity
  - transformers
---


<h1 align="center">FlagEmbedding</h1>


<h4 align="center">
    <p>
        <a href=#模型列表>模型列表</a> |
        <a href=#常见问题>常见问题</a> |
        <a href=#使用方法>使用方法</a>  |
        <a href="#评测">评测</a> |
        <a href="#训练">训练</a> |
        <a href="#联系我们">联系我们</a> |
        <a href="#引用">引用</a> |
        <a href="#许可证">许可证</a>
    <p>
</h4>

更多细节请参阅我们的 Github：[FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding)。

如果你需要支持更多语言、更长文本以及其他检索方式的模型，可以尝试使用 [bge-m3](https://huggingface.co/BAAI/bge-m3)。


[English](README.md) | [中文](https://github.com/FlagOpen/FlagEmbedding/blob/master/README_zh.md)

FlagEmbedding 专注于检索增强的大语言模型（retrieval-augmented LLMs），目前包含以下项目：

- **长上下文 LLM**： [Activation Beacon](https://github.com/FlagOpen/FlagEmbedding/tree/master/Long_LLM/activation_beacon)
- **语言模型微调**： [LM-Cocktail](https://github.com/FlagOpen/FlagEmbedding/tree/master/LM_Cocktail)
- **稠密检索**： [BGE-M3](https://github.com/FlagOpen/FlagEmbedding/tree/master/FlagEmbedding/BGE_M3)、[LLM Embedder](https://github.com/FlagOpen/FlagEmbedding/tree/master/FlagEmbedding/llm_embedder)、[BGE Embedding](https://github.com/FlagOpen/FlagEmbedding/tree/master/FlagEmbedding/baai_general_embedding)
- **重排序模型**： [BGE Reranker](https://github.com/FlagOpen/FlagEmbedding/tree/master/FlagEmbedding/reranker)
- **评测基准**： [C-MTEB](https://github.com/FlagOpen/FlagEmbedding/tree/master/C_MTEB)

## 更新动态
- 2024/1/30：发布 **BGE-M3**，BGE 系列新成员！M3 代表 **M**ulti-linguality（多语言，支持 100+ 种语言）、**M**ulti-granularities（多粒度，输入长度最长 8192）、**M**ulti-Functionality（多功能，统一稠密、稀疏、多向量/ColBERT 检索）。
这是首个同时支持三种检索方法的嵌入模型，并在多语言（MIRACL）与跨语言（MKQA）基准上取得新的 SOTA。
[技术报告](https://github.com/FlagOpen/FlagEmbedding/blob/master/FlagEmbedding/BGE_M3/BGE_M3.pdf) 与 [代码](https://github.com/FlagOpen/FlagEmbedding/tree/master/FlagEmbedding/BGE_M3)。 :fire:
- 2024/1/9：发布 [Activation-Beacon](https://github.com/FlagOpen/FlagEmbedding/tree/master/Long_LLM/activation_beacon)，一种有效、高效、兼容且训练成本低的方法，用于扩展 LLM 的上下文长度。[技术报告](https://arxiv.org/abs/2401.03462) :fire:
- 2023/12/24：发布 **LLaRA**，基于 LLaMA-7B 的稠密检索器，在 MS MARCO 与 BEIR 上达到领先性能。模型与代码即将开源，敬请期待。[技术报告](https://arxiv.org/abs/2312.15503) :fire:
- 2023/11/23：发布 [LM-Cocktail](https://github.com/FlagOpen/FlagEmbedding/tree/master/LM_Cocktail)，通过合并多个语言模型，在微调过程中保持通用能力的方法。[技术报告](https://arxiv.org/abs/2311.13534) :fire:
- 2023/10/12：发布 [LLM-Embedder](https://github.com/FlagOpen/FlagEmbedding/tree/master/FlagEmbedding/llm_embedder)，统一的嵌入模型，支持 LLM 多样化的检索增强需求。[技术报告](https://arxiv.org/pdf/2310.07554.pdf)
- 2023/09/15：BGE 的 [技术报告](https://arxiv.org/pdf/2309.07597.pdf) 与 [大规模训练数据](https://data.baai.ac.cn/details/BAAI-MTP) 已发布
- 2023/09/12：新模型：
    - **新的重排序模型**：发布交叉编码器模型 `BAAI/bge-reranker-base` 与 `BAAI/bge-reranker-large`，比嵌入模型更强。建议用它们对嵌入模型召回的 top-k 文档进行重排序，也可对其进行微调。
    - **更新嵌入模型**：发布 `bge-*-v1.5` 嵌入模型，缓解相似度分布问题，并在不使用 instruction 时增强检索能力。


<details>
  <summary>更多</summary>
<!-- ### 更多 -->

- 2023/09/07：更新 [微调代码](https://github.com/FlagOpen/FlagEmbedding/blob/master/FlagEmbedding/baai_general_embedding/README.md)：新增挖掘困难负样本（hard negatives）的脚本，并支持在微调时添加 instruction。
- 2023/08/09：BGE 模型已集成到 **Langchain**，可按[这种方式](#使用-langchain)使用；C-MTEB **排行榜**已[上线](https://huggingface.co/spaces/mteb/leaderboard)。
- 2023/08/05：发布 base 与 small 规模模型，**同尺寸模型中性能最佳 🤗**
- 2023/08/02：发布 `bge-large-*`（BAAI General Embedding 的简称）模型，**在 MTEB 与 C-MTEB 基准上排名第一！** :tada: :tada:
- 2023/08/01：我们发布了 [中文大规模文本嵌入评测基准](https://github.com/FlagOpen/FlagEmbedding/blob/master/C_MTEB)（**C-MTEB**），包含 31 个测试数据集。

</details>


## 模型列表

`bge` 是 `BAAI general embedding` 的缩写。

|              模型              | 语言 | | 说明 | 检索任务的 query instruction [1] |
|:-------------------------------|:--------:| :--------:| :--------:|:--------:|
| [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3)                   |    多语言     |    [推理](https://github.com/FlagOpen/FlagEmbedding/tree/master/FlagEmbedding/BGE_M3#usage) [微调](https://github.com/FlagOpen/FlagEmbedding/tree/master/FlagEmbedding/BGE_M3)    | 多功能（稠密检索、稀疏检索、多向量 ColBERT）、多语言、多粒度（8192 tokens） |  |
|  [BAAI/llm-embedder](https://huggingface.co/BAAI/llm-embedder)  |   英语 | [推理](./FlagEmbedding/llm_embedder/README.md) [微调](./FlagEmbedding/llm_embedder/README.md) | 统一嵌入模型，支持 LLM 多样化的检索增强需求 | 见 [README](./FlagEmbedding/llm_embedder/README.md) |
|  [BAAI/bge-reranker-large](https://huggingface.co/BAAI/bge-reranker-large)  |   中英双语 | [推理](#重排序模型的使用) [微调](https://github.com/FlagOpen/FlagEmbedding/tree/master/examples/reranker) | 交叉编码器模型，更准确但效率较低 [2] |   |
|  [BAAI/bge-reranker-base](https://huggingface.co/BAAI/bge-reranker-base) |   中英双语 | [推理](#重排序模型的使用) [微调](https://github.com/FlagOpen/FlagEmbedding/tree/master/examples/reranker) | 交叉编码器模型，更准确但效率较低 [2] |   |
|  [BAAI/bge-large-en-v1.5](https://huggingface.co/BAAI/bge-large-en-v1.5) |   英语 | [推理](#嵌入模型的使用) [微调](https://github.com/FlagOpen/FlagEmbedding/tree/master/examples/finetune) | 1.5 版本，相似度分布更合理 | `Represent this sentence for searching relevant passages: `  |
|  [BAAI/bge-base-en-v1.5](https://huggingface.co/BAAI/bge-base-en-v1.5) |   英语 | [推理](#嵌入模型的使用) [微调](https://github.com/FlagOpen/FlagEmbedding/tree/master/examples/finetune) | 1.5 版本，相似度分布更合理 | `Represent this sentence for searching relevant passages: `  |
|  [BAAI/bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5) |   英语 | [推理](#嵌入模型的使用) [微调](https://github.com/FlagOpen/FlagEmbedding/tree/master/examples/finetune) | 1.5 版本，相似度分布更合理  | `Represent this sentence for searching relevant passages: `  |
|  [BAAI/bge-large-zh-v1.5](https://huggingface.co/BAAI/bge-large-zh-v1.5) |   中文 | [推理](#嵌入模型的使用) [微调](https://github.com/FlagOpen/FlagEmbedding/tree/master/examples/finetune) | 1.5 版本，相似度分布更合理 | `为这个句子生成表示以用于检索相关文章：`  |
|  [BAAI/bge-base-zh-v1.5](https://huggingface.co/BAAI/bge-base-zh-v1.5) |   中文 |  [推理](#嵌入模型的使用) [微调](https://github.com/FlagOpen/FlagEmbedding/tree/master/examples/finetune) | 1.5 版本，相似度分布更合理 | `为这个句子生成表示以用于检索相关文章：`  |
|  [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5) |   中文 | [推理](#嵌入模型的使用) [微调](https://github.com/FlagOpen/FlagEmbedding/tree/master/examples/finetune) | 1.5 版本，相似度分布更合理 | `为这个句子生成表示以用于检索相关文章：`  |
|  [BAAI/bge-large-en](https://huggingface.co/BAAI/bge-large-en) |   英语 | [推理](#嵌入模型的使用) [微调](https://github.com/FlagOpen/FlagEmbedding/tree/master/examples/finetune) | :trophy: 在 [MTEB](https://huggingface.co/spaces/mteb/leaderboard) 排行榜排名 **第 1** | `Represent this sentence for searching relevant passages: `  |
|  [BAAI/bge-base-en](https://huggingface.co/BAAI/bge-base-en) |   英语 | [推理](#嵌入模型的使用) [微调](https://github.com/FlagOpen/FlagEmbedding/tree/master/examples/finetune) | base 规模模型，能力接近 `bge-large-en` | `Represent this sentence for searching relevant passages: `  |
|  [BAAI/bge-small-en](https://huggingface.co/BAAI/bge-small-en) |   英语 | [推理](#嵌入模型的使用) [微调](https://github.com/FlagOpen/FlagEmbedding/tree/master/examples/finetune) | small 规模模型，性能具有竞争力  | `Represent this sentence for searching relevant passages: `  |
|  [BAAI/bge-large-zh](https://huggingface.co/BAAI/bge-large-zh) |   中文 | [推理](#嵌入模型的使用) [微调](https://github.com/FlagOpen/FlagEmbedding/tree/master/examples/finetune) | :trophy: 在 [C-MTEB](https://github.com/FlagOpen/FlagEmbedding/tree/master/C_MTEB) 基准排名 **第 1** | `为这个句子生成表示以用于检索相关文章：`  |
|  [BAAI/bge-base-zh](https://huggingface.co/BAAI/bge-base-zh) |   中文 |  [推理](#嵌入模型的使用) [微调](https://github.com/FlagOpen/FlagEmbedding/tree/master/examples/finetune) | base 规模模型，能力接近 `bge-large-zh` | `为这个句子生成表示以用于检索相关文章：`  |
|  [BAAI/bge-small-zh](https://huggingface.co/BAAI/bge-small-zh) |   中文 | [推理](#嵌入模型的使用) [微调](https://github.com/FlagOpen/FlagEmbedding/tree/master/examples/finetune) | small 规模模型，性能具有竞争力 | `为这个句子生成表示以用于检索相关文章：`  |

[1\]：如果需要根据 query 搜索相关段落，建议为 query 添加 instruction；其他情况下无需 instruction，直接使用原始 query。在所有情况下，**都不要**为段落（passage）添加 instruction。

[2\]：与嵌入模型不同，重排序模型以问题和文档为输入，直接输出相似度而非嵌入向量。为平衡精度与耗时，交叉编码器常用于对其他简单模型召回的 top-k 文档做重排序。
例如：先用 bge 嵌入模型召回 top 100 相关文档，再用 bge 重排序模型对这 100 篇文档重排，得到最终的 top-3 结果。

所有模型已上传至 Huggingface Hub，可在 https://huggingface.co/BAAI 查看。
若无法访问 Huggingface Hub，也可在 https://model.baai.ac.cn/models 下载模型。


## 常见问题

<details>
  <summary>1. 如何微调 bge 嵌入模型？</summary>

  <!-- ### 如何微调 bge 嵌入模型？ -->
请参考此[示例](https://github.com/FlagOpen/FlagEmbedding/tree/master/examples/finetune)准备数据并微调模型。
一些建议：
- 按此[示例](https://github.com/FlagOpen/FlagEmbedding/tree/master/examples/finetune#hard-negatives)挖掘困难负样本，可提升检索性能。
- 若在自有数据上对 bge 做预训练，预训练后的模型不能直接用于计算相似度，必须先通过对比学习微调后再计算相似度。
- 若微调后准确率仍不够高，建议使用/微调交叉编码器模型（bge-reranker）对 top-k 结果重排序。微调重排序模型同样需要困难负样本。


</details>

<details>
  <summary>2. 两个不相似句子的相似度分数高于 0.5</summary>

  <!-- ### 两个不相似句子的相似度分数高于 0.5 -->
**建议使用 bge v1.5，它缓解了相似度分布问题。**

由于我们使用温度为 0.01 的对比学习对模型进行微调，
当前 BGE 模型的相似度分布大约落在区间 \[0.6, 1\]。
因此相似度分数大于 0.5 并不意味着两个句子相似。

对于下游任务（如段落检索或语义相似度），
**重要的是分数的相对排序，而非绝对值。**
若需要根据相似度阈值过滤相似句子，
请根据你数据上的相似度分布选择合适阈值（如 0.8、0.85，甚至 0.9）。

</details>

<details>
  <summary>3. 什么时候需要使用 query instruction</summary>

  <!-- ### 什么时候需要使用 query instruction -->

对于 `bge-*-v1.5`，我们提升了其在不使用 instruction 时的检索能力。
不使用 instruction 相比使用 instruction，检索性能仅略有下降。
因此为方便起见，你可以在所有场景下都不加 instruction 来生成嵌入。

对于用短 query 检索长相关文档的检索任务，
建议为这些短 query 添加 instruction。
**判断是否为 query 添加 instruction 的最佳方式，是选择在你的任务上效果更好的设置。**
在所有情况下，文档/段落都无需添加 instruction。

</details>


## 使用方法

### 嵌入模型的使用

以下是使用 `bge` 模型的一些示例，可通过
[FlagEmbedding](#使用-flagembedding)、[Sentence-Transformers](#使用-sentence-transformers)、[Langchain](#使用-langchain) 或 [Huggingface Transformers](#使用-huggingface-transformers) 使用。

#### 使用 FlagEmbedding
```
pip install -U FlagEmbedding
```
如果安装不成功，可参阅 [FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding/blob/master/FlagEmbedding/baai_general_embedding/README.md) 了解更多安装方式。

```python
from FlagEmbedding import FlagModel
sentences_1 = ["样例数据-1", "样例数据-2"]
sentences_2 = ["样例数据-3", "样例数据-4"]
model = FlagModel('BAAI/bge-large-zh-v1.5',
                  query_instruction_for_retrieval="为这个句子生成表示以用于检索相关文章：",
                  use_fp16=True) # 将 use_fp16 设为 True 可加速计算，性能略有下降
embeddings_1 = model.encode(sentences_1)
embeddings_2 = model.encode(sentences_2)
similarity = embeddings_1 @ embeddings_2.T
print(similarity)

# 对于 s2p（短 query 检索长段落）任务，建议使用 encode_queries()，它会自动为每个 query 添加 instruction
# 检索任务中的语料库仍可使用 encode() 或 encode_corpus()，因为它们不需要 instruction
queries = ['query_1', 'query_2']
passages = ["样例文档-1", "样例文档-2"]
q_embeddings = model.encode_queries(queries)
p_embeddings = model.encode(passages)
scores = q_embeddings @ p_embeddings.T
```
参数 `query_instruction_for_retrieval` 的取值见 [模型列表](https://github.com/FlagOpen/FlagEmbedding/tree/master#model-list)。

默认情况下，FlagModel 编码时会使用所有可用 GPU。请设置 `os.environ["CUDA_VISIBLE_DEVICES"]` 来选择特定 GPU。
也可设置 `os.environ["CUDA_VISIBLE_DEVICES"]=""` 以禁用所有 GPU。


#### 使用 Sentence-Transformers

你也可以通过 [sentence-transformers](https://www.SBERT.net) 使用 `bge` 模型：

```
pip install -U sentence-transformers
```
```python
from sentence_transformers import SentenceTransformer
sentences_1 = ["样例数据-1", "样例数据-2"]
sentences_2 = ["样例数据-3", "样例数据-4"]
model = SentenceTransformer('BAAI/bge-large-zh-v1.5')
embeddings_1 = model.encode(sentences_1, normalize_embeddings=True)
embeddings_2 = model.encode(sentences_2, normalize_embeddings=True)
similarity = embeddings_1 @ embeddings_2.T
print(similarity)
```
对于 s2p（短 query 检索长段落）任务，
每个短 query 应以 instruction 开头（instruction 见 [模型列表](https://github.com/FlagOpen/FlagEmbedding/tree/master#model-list)）。
但段落不需要 instruction。
```python
from sentence_transformers import SentenceTransformer
queries = ['query_1', 'query_2']
passages = ["样例文档-1", "样例文档-2"]
instruction = "为这个句子生成表示以用于检索相关文章："

model = SentenceTransformer('BAAI/bge-large-zh-v1.5')
q_embeddings = model.encode([instruction+q for q in queries], normalize_embeddings=True)
p_embeddings = model.encode(passages, normalize_embeddings=True)
scores = q_embeddings @ p_embeddings.T
```

#### 使用 Langchain

可以像这样在 langchain 中使用 `bge`：
```python
from langchain.embeddings import HuggingFaceBgeEmbeddings
model_name = "BAAI/bge-large-en-v1.5"
model_kwargs = {'device': 'cuda'}
encode_kwargs = {'normalize_embeddings': True} # 设为 True 以计算余弦相似度
model = HuggingFaceBgeEmbeddings(
    model_name=model_name,
    model_kwargs=model_kwargs,
    encode_kwargs=encode_kwargs,
    query_instruction="为这个句子生成表示以用于检索相关文章："
)
model.query_instruction = "为这个句子生成表示以用于检索相关文章："
```


#### 使用 HuggingFace Transformers

使用 transformers 包时，可以这样使用模型：先将输入传入 transformer 模型，再选取第一个 token（即 [CLS]）的最后一层隐状态作为句向量。

```python
from transformers import AutoTokenizer, AutoModel
import torch
# 需要生成句向量的句子
sentences = ["样例数据-1", "样例数据-2"]

# 从 HuggingFace Hub 加载模型
tokenizer = AutoTokenizer.from_pretrained('BAAI/bge-large-zh-v1.5')
model = AutoModel.from_pretrained('BAAI/bge-large-zh-v1.5')
model.eval()

# 对句子进行分词
encoded_input = tokenizer(sentences, padding=True, truncation=True, return_tensors='pt')
# 对于 s2p（短 query 检索长段落）任务，为 query 添加 instruction（段落不加 instruction）
# encoded_input = tokenizer([instruction + q for q in queries], padding=True, truncation=True, return_tensors='pt')

# 计算 token 嵌入
with torch.no_grad():
    model_output = model(**encoded_input)
    # 池化。此处使用 cls pooling。
    sentence_embeddings = model_output[0][:, 0]
# 归一化嵌入
sentence_embeddings = torch.nn.functional.normalize(sentence_embeddings, p=2, dim=1)
print("Sentence embeddings:", sentence_embeddings)
```

### 重排序模型的使用

与嵌入模型不同，重排序模型以问题和文档为输入，直接输出相似度而非嵌入向量。
将 query 与 passage 输入重排序模型，即可得到相关性分数。
该重排序模型基于交叉熵损失优化，因此相关性分数不限制在特定区间内。


#### 使用 FlagEmbedding
```
pip install -U FlagEmbedding
```

获取相关性分数（分数越高表示越相关）：
```python
from FlagEmbedding import FlagReranker
reranker = FlagReranker('BAAI/bge-reranker-large', use_fp16=True) # 将 use_fp16 设为 True 可加速计算，性能略有下降

score = reranker.compute_score(['query', 'passage'])
print(score)

scores = reranker.compute_score([['what is panda?', 'hi'], ['what is panda?', 'The giant panda (Ailuropoda melanoleuca), sometimes called a panda bear or simply panda, is a bear species endemic to China.']])
print(scores)
```


#### 使用 Huggingface transformers

```python
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained('BAAI/bge-reranker-large')
model = AutoModelForSequenceClassification.from_pretrained('BAAI/bge-reranker-large')
model.eval()

pairs = [['what is panda?', 'hi'], ['what is panda?', 'The giant panda (Ailuropoda melanoleuca), sometimes called a panda bear or simply panda, is a bear species endemic to China.']]
with torch.no_grad():
    inputs = tokenizer(pairs, padding=True, truncation=True, return_tensors='pt', max_length=512)
    scores = model(**inputs, return_dict=True).logits.view(-1, ).float()
    print(scores)
```

## 评测

`baai-general-embedding` 模型在 **MTEB 与 C-MTEB 排行榜上均达到最先进水平！**
更多细节与评测工具见我们的 [脚本](https://github.com/FlagOpen/FlagEmbedding/blob/master/C_MTEB/README.md)。

- **MTEB**：

| 模型名称 |  维度 | 序列长度 | 平均 (56) | 检索 (15) |聚类 (11) | 配对分类 (3) | 重排序 (4) |  STS (10) | 摘要 (1) | 分类 (12) |
|:----:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| [BAAI/bge-large-en-v1.5](https://huggingface.co/BAAI/bge-large-en-v1.5) | 1024 | 512 |  **64.23** | **54.29** |  46.08 | 87.12 | 60.03 | 83.11 | 31.61 | 75.97 |
| [BAAI/bge-base-en-v1.5](https://huggingface.co/BAAI/bge-base-en-v1.5) |  768 | 512 | 63.55 | 53.25 |   45.77 | 86.55 | 58.86 | 82.4 | 31.07 | 75.53 |
| [BAAI/bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5) |  384 | 512 | 62.17 |51.68 | 43.82 |  84.92 | 58.36 | 81.59 | 30.12 | 74.14 |
| [bge-large-en](https://huggingface.co/BAAI/bge-large-en) |  1024 | 512 | 63.98 |  53.9 | 46.98 | 85.8 | 59.48 | 81.56 | 32.06 | 76.21 |
| [bge-base-en](https://huggingface.co/BAAI/bge-base-en) |  768 | 512 |  63.36 | 53.0 | 46.32 | 85.86 | 58.7 | 81.84 | 29.27 | 75.27 |
| [gte-large](https://huggingface.co/thenlper/gte-large) |  1024 | 512 | 63.13 | 52.22 | 46.84 | 85.00 | 59.13 | 83.35 | 31.66 | 73.33 |
| [gte-base](https://huggingface.co/thenlper/gte-base) 	|  768 | 512 | 62.39 | 51.14 | 46.2 | 84.57 | 58.61 | 82.3 | 31.17 | 73.01 |
| [e5-large-v2](https://huggingface.co/intfloat/e5-large-v2) |  1024| 512 | 62.25 | 50.56 | 44.49 | 86.03 | 56.61 | 82.05 | 30.19 | 75.24 |
| [bge-small-en](https://huggingface.co/BAAI/bge-small-en) |  384 | 512 | 62.11 |  51.82 | 44.31 | 83.78 | 57.97 | 80.72 | 30.53 | 74.37 |
| [instructor-xl](https://huggingface.co/hkunlp/instructor-xl) |  768 | 512 | 61.79 | 49.26 | 44.74 | 86.62 | 57.29 | 83.06 | 32.32 | 61.79 |
| [e5-base-v2](https://huggingface.co/intfloat/e5-base-v2) |  768 | 512 | 61.5 | 50.29 | 43.80 | 85.73 | 55.91 | 81.05 | 30.28 | 73.84 |
| [gte-small](https://huggingface.co/thenlper/gte-small) |  384 | 512 | 61.36 | 49.46 | 44.89 | 83.54 | 57.7 | 82.07 | 30.42 | 72.31 |
| [text-embedding-ada-002](https://platform.openai.com/docs/guides/embeddings) | 1536 | 8192 | 60.99 | 49.25 | 45.9 | 84.89 | 56.32 | 80.97 | 30.8 | 70.93 |
| [e5-small-v2](https://huggingface.co/intfloat/e5-base-v2) | 384 | 512 | 59.93 | 49.04 | 39.92 | 84.67 | 54.32 | 80.39 | 31.16 | 72.94 |
| [sentence-t5-xxl](https://huggingface.co/sentence-transformers/sentence-t5-xxl) |  768 | 512 | 59.51 | 42.24 | 43.72 | 85.06 | 56.42 | 82.63 | 30.08 | 73.42 |
| [all-mpnet-base-v2](https://huggingface.co/sentence-transformers/all-mpnet-base-v2) 	|  768 | 514 	| 57.78 | 43.81 | 43.69 | 83.04 | 59.36 | 80.28 | 27.49 | 65.07 |
| [sgpt-bloom-7b1-msmarco](https://huggingface.co/bigscience/sgpt-bloom-7b1-msmarco) 	|  4096 | 2048 | 57.59 | 48.22 | 38.93 | 81.9 | 55.65 | 77.74 | 33.6 | 66.19 |



- **C-MTEB**：
我们创建了中文文本嵌入评测基准 C-MTEB，包含来自 6 类任务的 31 个数据集。
详细介绍请参阅 [C_MTEB](https://github.com/FlagOpen/FlagEmbedding/blob/master/C_MTEB/README.md)。

| 模型 | 嵌入维度 | 平均 | 检索 | STS | 配对分类 | 分类 | 重排序 | 聚类 |
|:-------------------------------|:--------:|:--------:|:--------:|:--------:|:--------:|:--------:|:--------:|:--------:|
| [**BAAI/bge-large-zh-v1.5**](https://huggingface.co/BAAI/bge-large-zh-v1.5) | 1024 |  **64.53** | 70.46 | 56.25 | 81.6 | 69.13 | 65.84 | 48.99 |
| [BAAI/bge-base-zh-v1.5](https://huggingface.co/BAAI/bge-base-zh-v1.5) | 768 |  63.13 | 69.49 | 53.72 | 79.75 | 68.07 | 65.39 | 47.53 |
| [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5) | 512 | 57.82 | 61.77 | 49.11 | 70.41 | 63.96 | 60.92 | 44.18 |
| [BAAI/bge-large-zh](https://huggingface.co/BAAI/bge-large-zh) | 1024 | 64.20 | 71.53 | 54.98 | 78.94 | 68.32 | 65.11 | 48.39 |
| [bge-large-zh-noinstruct](https://huggingface.co/BAAI/bge-large-zh-noinstruct) | 1024 | 63.53 | 70.55 | 53 | 76.77 | 68.58 | 64.91 | 50.01 |
| [BAAI/bge-base-zh](https://huggingface.co/BAAI/bge-base-zh) | 768 | 62.96 | 69.53 | 54.12 | 77.5 | 67.07 | 64.91 | 47.63 |
| [multilingual-e5-large](https://huggingface.co/intfloat/multilingual-e5-large) | 1024 | 58.79 | 63.66 | 48.44 | 69.89 | 67.34 | 56.00 | 48.23 |
| [BAAI/bge-small-zh](https://huggingface.co/BAAI/bge-small-zh) | 512 | 58.27 |  63.07 | 49.45 | 70.35 | 63.64 | 61.48 | 45.09 |
| [m3e-base](https://huggingface.co/moka-ai/m3e-base) | 768 | 57.10 | 56.91 | 50.47 | 63.99 | 67.52 | 59.34 | 47.68 |
| [m3e-large](https://huggingface.co/moka-ai/m3e-large) | 1024 |  57.05 | 54.75 | 50.42 | 64.3 | 68.2 | 59.66 | 48.88 |
| [multilingual-e5-base](https://huggingface.co/intfloat/multilingual-e5-base) | 768 | 55.48 | 61.63 | 46.49 | 67.07 | 65.35 | 54.35 | 40.68 |
| [multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small) | 384 | 55.38 | 59.95 | 45.27 | 66.45 | 65.85 | 53.86 | 45.26 |
| [text-embedding-ada-002(OpenAI)](https://platform.openai.com/docs/guides/embeddings/what-are-embeddings) | 1536 |  53.02 | 52.0 | 43.35 | 69.56 | 64.31 | 54.28 | 45.68 |
| [luotuo](https://huggingface.co/silk-road/luotuo-bert-medium) | 1024 | 49.37 |  44.4 | 42.78 | 66.62 | 61 | 49.25 | 44.39 |
| [text2vec-base](https://huggingface.co/shibing624/text2vec-base-chinese) | 768 |  47.63 | 38.79 | 43.41 | 67.41 | 62.19 | 49.45 | 37.66 |
| [text2vec-large](https://huggingface.co/GanymedeNil/text2vec-large-chinese) | 1024 | 47.36 | 41.94 | 44.97 | 70.86 | 60.66 | 49.16 | 30.02 |


- **重排序**：
评测脚本见 [C_MTEB](https://github.com/FlagOpen/FlagEmbedding/blob/master/C_MTEB/)。

| 模型 | T2Reranking | T2RerankingZh2En\* | T2RerankingEn2Zh\* | MMarcoReranking | CMedQAv1 | CMedQAv2 | 平均 |
|:-------------------------------|:--------:|:--------:|:--------:|:--------:|:--------:|:--------:|:--------:|
| text2vec-base-multilingual | 64.66 | 62.94 | 62.51 | 14.37 | 48.46 | 48.6 | 50.26 |
| multilingual-e5-small | 65.62 | 60.94 | 56.41 | 29.91 | 67.26 | 66.54 | 57.78 |
| multilingual-e5-large | 64.55 | 61.61 | 54.28 | 28.6 | 67.42 | 67.92 | 57.4 |
| multilingual-e5-base | 64.21 | 62.13 | 54.68 | 29.5 | 66.23 | 66.98 | 57.29 |
| m3e-base | 66.03 | 62.74 | 56.07 | 17.51 | 77.05 | 76.76 | 59.36 |
| m3e-large | 66.13 | 62.72 | 56.1 | 16.46 | 77.76 | 78.27 | 59.57 |
| bge-base-zh-v1.5 | 66.49 | 63.25 | 57.02 | 29.74 | 80.47 | 84.88 | 63.64 |
| bge-large-zh-v1.5 | 65.74 | 63.39 | 57.03 | 28.74 | 83.45 | 85.44 | 63.97 |
| [BAAI/bge-reranker-base](https://huggingface.co/BAAI/bge-reranker-base) | 67.28 | 63.95 | 60.45 | 35.46 | 81.26 | 84.1 | 65.42 |
| [BAAI/bge-reranker-large](https://huggingface.co/BAAI/bge-reranker-large) | 67.6 | 64.03 | 61.44 | 37.16 | 82.15 | 84.18 | 66.09 |

\*：T2RerankingZh2En 与 T2RerankingEn2Zh 为跨语言检索任务

## 训练

### BAAI Embedding

我们使用 [retromae](https://github.com/staoxiao/RetroMAE) 对模型进行预训练，并在大规模配对数据上通过对比学习训练。
**你可以参考我们的[示例](https://github.com/FlagOpen/FlagEmbedding/tree/master/examples/finetune)，在自有数据上微调嵌入模型。**
我们也提供了[预训练示例](https://github.com/FlagOpen/FlagEmbedding/tree/master/examples/pretrain)。
注意：预训练的目标是重建文本，预训练后的模型不能直接用于相似度计算，需要再微调。
bge 更多训练细节见 [baai_general_embedding](https://github.com/FlagOpen/FlagEmbedding/blob/master/FlagEmbedding/baai_general_embedding/README.md)。



### BGE Reranker

交叉编码器会对输入对做全注意力计算，
比嵌入模型（即双编码器）更准确，但也更耗时。
因此可用于对嵌入模型返回的 top-k 文档做重排序。
我们在多语言配对数据上训练交叉编码器，
数据格式与嵌入模型相同，因此可以很容易地按我们的[示例](https://github.com/FlagOpen/FlagEmbedding/tree/master/examples/reranker)进行微调。
更多细节请参阅 [./FlagEmbedding/reranker/README.md](https://github.com/FlagOpen/FlagEmbedding/tree/master/FlagEmbedding/reranker)


## 联系我们
如对本项目有任何问题或建议，欢迎提交 issue 或 pull request。
也可发邮件给 Shitao Xiao（stxiao@baai.ac.cn）和 Zheng Liu（liuzheng@baai.ac.cn）。


## 引用

如果本仓库对你有帮助，欢迎点亮 star :star: 并引用

```
@misc{bge_embedding,
      title={C-Pack: Packaged Resources To Advance General Chinese Embedding},
      author={Shitao Xiao and Zheng Liu and Peitian Zhang and Niklas Muennighoff},
      year={2023},
      eprint={2309.07597},
      archivePrefix={arXiv},
      primaryClass={cs.CL}
}
```

## 许可证
FlagEmbedding 采用 [MIT License](https://github.com/FlagOpen/FlagEmbedding/blob/master/LICENSE) 许可。发布的模型可免费用于商业用途。
