#!/bin/bash

SKYT_HOME="/ephemeral/peiyao-workspace/SkyThought"

MODEL="Qwen/QwQ-32B-Preview"
# Uncomment and modify as needed:
# MODEL="Qwen/QwQ-32B-Preview deepseek-ai/DeepSeek-R1-Distill-Qwen-32B"
# MODEL="Qwen/Qwen2.5-1.5B-Instruct"

MODEL_SHORT=$(echo "$MODEL" | awk -F'/' '{print $2}')
TASK="supervised_atcoder"
START=0
END=-1

#OUTPUT_LOG="atcoder-supervised.log"
OUTPUT_LOG="${MODEL_SHORT}_${TASK}_${START}_${END}.log"
echo $OUTPUT_LOG
# Run inference script in the background
nohup python inference_and_check.py \
    --task "$TASK" \
    --model "$MODEL" \
    --tp 8 \
    --start "$START" \
    --end "$END" \
    --max_tokens 8192 \
    --result-dir "$SKYT_HOME/data" \
    --check > "$OUTPUT_LOG" 2>&1 &

# Additional optional parameters (commented out)
#    --temperatures 1 \
#    --n 8 \
