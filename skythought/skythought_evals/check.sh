#!/bin/bash

SKYT_HOME="/ephemeral/peiyao-workspace/SkyThought"
OUTPUT_LOG="check-log.txt"
MODEL="Qwen/QwQ-32B-Preview"
#MODEL="Qwen/QwQ-32B-Preview""deepseek-ai/DeepSeek-R1-Distill-Qwen-32B"
#Qwen/Qwen2.5-1.5B-Instruct" 

#CUDA_VISIBLE_DEVICES=2,3,4,7 
nohup python inference_and_check.py \
    --task atcoder \
    --model "$MODEL"\
    --tp 8 \
    --max_tokens 8192 \
    --result-dir $SKYT_HOME/data \
    --check > $OUTPUT_LOG 2>&1 &


