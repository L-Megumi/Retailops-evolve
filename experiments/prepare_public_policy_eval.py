"""Export the reviewed public-policy subset for external-model experiments."""
import argparse
import json
from pathlib import Path
from retailops_agent.knowledge import is_official_source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("runs/public-policy-eval"))
    parser.add_argument("--ids", help="optional comma-separated scenario IDs")
    parser.add_argument("--questions", type=Path, default=Path("benchmarks/knowledge/questions.json"))
    args = parser.parse_args()
    corpus = json.loads(Path("knowledge/retailops-policies.json").read_text(encoding="utf-8"))
    documents = [d for d in corpus["documents"] if d["source_type"] == "official_public_policy"
                 and is_official_source(d["platform"], d["source_url"])]
    questions = json.loads(args.questions.read_text(encoding="utf-8"))
    selected = set(args.ids.split(",")) if args.ids else None
    questions = [q for q in questions if "retailops" not in q.get("platforms", [])
                 and "retailops" not in q["query"].lower() and "内部" not in q["query"]
                 and (selected is None or q["id"] in selected)]
    args.output.mkdir(parents=True, exist_ok=True)
    for name, payload in (("corpus.json", {"schema_version": 1, "documents": documents}), ("questions.json", questions)):
        (args.output / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"documents": len(documents), "questions": len(questions), "output": str(args.output)}))


if __name__ == "__main__":
    main()
