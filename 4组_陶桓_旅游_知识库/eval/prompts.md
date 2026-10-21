## 生成评估测试集
【核心目标】
    生成一套用于ragas 评估的测试集
【已知背景】
    待评估的资料文档已经放在了 eval\hak180产品安全手册_new.md ，请基于资料生成问题集
【具体要求】
   1  生成问题集 格式为csv ，编码集 utf-8 bom 
   2  保存在 eval/qa.csv 
   3  包括两列： question, ground_truth
   4  10对问题和答案
   5  生成的问题必须以“HAK 180 烫金机：” 作为问题的前缀 


## 生成评估测试程序
【核心目标】
    生成一份基于ragas的评估测试程序
【已知背景】
    1  需要评估的测试集已经放在 eval\qa.csv
    2  待评估的rag程序的入口在 knowledge\processor\query_process\main_graph.py 中的 query_app.invoke()
    3  需要的llm 和 embedding的客户端工具在knowledge\utils\llm_client_util.py 和 knowledge\utils\bge_m3_embedding_util.py 中 
【具体要求】
    1  评估要求产生5个指标： Faithfulness、Answer Relevancy、Context Precision、Context Recall、Answer Correctness
    2  评估最终生成的文件 result.csv 保存在 eval/result.csv
    3  生成的评估程序 eval.py 保存在 eval/eval.py
    4  生成的函数要求有中文注释 
    5  所有的核心步骤函数 都要以 step_1_xx  step_2_xx  step_3_xx  step_4_xx  step_5_xx ...的形式作为命名前缀
