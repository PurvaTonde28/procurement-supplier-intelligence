"""Scores Phase 5's RAG pipeline: does it cite the RIGHT contract/page?"""
import json
from app.retrieval.hybrid_search import hybrid_search
from app.retrieval.rerank import rerank_top3


def eval_citations(conn, tenant_id: str) -> dict:
    with open("data/eval/extraction_golden.json") as f:
        golden = json.load(f)

    results = []
    for case in golden["citation_questions"]:
        candidates = hybrid_search(conn, tenant_id, case["question"], top_k=8)
        reranked = rerank_top3(conn, tenant_id, case["question"], candidates)
        conn.commit()

        top_result = reranked.results[0] if reranked.results else None
        correct = (
            top_result is not None and
            top_result.contract_number == case["expected_contract"] and
            top_result.page_number == case["expected_page"]
        )
        results.append({
            "question": case["question"], "correct": correct,
            "expected": f"{case['expected_contract']}/p{case['expected_page']}",
            "actual": f"{top_result.contract_number}/p{top_result.page_number}" if top_result else "none"
        })

    accuracy = sum(1 for r in results if r["correct"]) / len(results)
    return {"citation_accuracy": accuracy, "details": results}