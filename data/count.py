import json

def count_incorrect_responses(json_file):
    with open(json_file, 'r', encoding='utf-8') as file:
        data = json.load(file)

    incorrect_count = 0
    reason_counts = {}

    for key, value in data.items():
        if "responses" in value:
            for response_key, response_list in value["responses"].items():
                for response in response_list:
                    if response.get("correctness") is False:
                        incorrect_count += 1
                        reason = response.get("reason", "No reason provided")
                        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    return incorrect_count, reason_counts

# Example usage
#json_file_path = "DeepSeek-R1-Distill-Qwen-32B_atcoder_all_None_False_0_-1.json"  
json_file_path = "QwQ-32B-Preview_atcoder_all_None_False_0_-1.json"
incorrect_count, reason_counts = count_incorrect_responses(json_file_path)

print(f"Total incorrect responses: {incorrect_count}")
print("Reasons for incorrect responses:")
for reason, count in reason_counts.items():
    print(f"- {reason}: {count} occurrences")

