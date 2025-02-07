import json
import re
import sys
config_name = sys.argv[1]
#"DeepSeek-R1-Distill-Qwen-32B_atcoder"
input_json_path = f"./data/{config_name}_all_None_False_0_-1.json"
output_json_path = f"./skythought/train/LLaMA-Factory/data/atcoder/{config_name}.json"

with open(input_json_path, 'r') as f:
    data = json.load(f)

results = []

def transform_input(problem_statement, editorial, lang, code):
    return {
        "system": "You are a helpful coding assistant tasked with assisting users in solving technical problems. Your role is to follow a structured reasoning approach before arriving at a precise solution. \n\nYour response should have two sections: Thought and Solution.\n\nIn the **Thought** section, outline your reasoning step by step using this format:\n\n<|begin_of_thought|>\n{Step-by-step logical process, each step separated by '\n\n'}\n<|end_of_thought|>\n\n The thought section should include analysis, summarization, brainstorming, verification, refinement, and backtracking when needed. \n\nIn the **Solution** section, present the final, correct solution concisely and clearly. The code must be complete, compilable, and executable in Python, formatted as:\n\n<|begin_of_solution|>\n{Final Python code solution}\n<|end_of_solution|>\n\nNow, solve the following problem using this framework:",
        "conversations": [
            {"from": "user", "value": problem_statement},
            {"from": "assistant", "value": f"<|begin_of_thought|>\n{editorial}\n<|end_of_thought|>\n\n<|begin_of_solution|>\n```python\n{code}\n```\n<|end_of_solution|>"}
        ]
    }
def has_code(response):
    #pattern = r"```(?:[a-zA-Z]*)\n(.*?)```"
    pattern = r"```(?:[a-zA-Z0-9]*)\n(.*?)```"
    # Use re.DOTALL to match multiline content inside backticks
    matches = re.findall(pattern, response, re.DOTALL)
    # print(matches)
    return matches

for problem_key, problem_data in data.items():
    problem_statement = problem_data.get("question", "")
    responses = problem_data.get("responses", {})

    for response_key, response_lst in responses.items():
        for response in response_lst:
            if response.get("correctness", False):
                response_content = response.get("content", "")
                code_filter_result = has_code(response_content)
                last_code = code_filter_result[-1]
                editorial = response_content.replace(last_code, "\nThe solution code is output at the end\n")
                lang = "py"

                results.append(transform_input(problem_statement, editorial, lang, last_code))

# Save the transformed dataset
with open(output_json_path, 'w') as output_json:
    json.dump(results, output_json, indent=4)

print(f"Dataset ({len(results)} entries) saved to {output_json_path}")

