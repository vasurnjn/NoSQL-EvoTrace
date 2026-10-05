"""Impact report generator and formatter."""

from __future__ import annotations
from typing import Any, Dict, List, Optional


def generate_impact_report(
    operation_dict: Dict[str, Any],
    provenance_dict: Optional[Dict[str, Any]],
    impact_analysis_dict: Dict[str, Any],
) -> Dict[str, Any]:
    """Assemble a consolidated impact assessment report."""
    op_type = operation_dict.get("operation_type", "unknown")
    col_name = operation_dict.get("collection_name", "unknown")
    affected_doc_ids = provenance_dict.get("affected_doc_ids", []) if provenance_dict else []
    query_impacts = impact_analysis_dict.get("query_impacts", [])

    high_risk_queries = [q for q in query_impacts if q.get("severity") == "HIGH"]
    medium_risk_queries = [q for q in query_impacts if q.get("severity") == "MEDIUM"]

    # Generate actionable recommendation
    if high_risk_queries:
        recommendation = (
            f"HIGH ATTENTION: {len(high_risk_queries)} registered queries will likely break or fail "
            "to return expected results. Update application code or query filters prior to deployment."
        )
    elif medium_risk_queries:
        recommendation = (
            f"MODERATE ATTENTION: {len(medium_risk_queries)} registered queries reference affected parent or "
            "child paths. Review application consumers to ensure field restructuring is handled."
        )
    else:
        recommendation = "LOW RISK: No registered application queries directly intersect with the transformed paths."

    return {
        "operation_id": operation_dict.get("operation_id"),
        "operation_type": op_type,
        "collection_name": col_name,
        "status": operation_dict.get("status"),
        "timestamp": operation_dict.get("executed_at") or operation_dict.get("created_at"),
        "total_documents_affected": len(affected_doc_ids),
        "sample_affected_doc_ids": affected_doc_ids[:10],
        "total_queries_analyzed": len(query_impacts),
        "high_risk_queries_count": len(high_risk_queries),
        "medium_risk_queries_count": len(medium_risk_queries),
        "recommendation": recommendation,
        "query_details": query_impacts,
    }
