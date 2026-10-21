# NoSQL-EvoTrace: Algorithmic Specifications & Mathematical Formulations

This document provides formal descriptions, pseudocode, and mathematical definitions for the algorithms implemented in NoSQL-EvoTrace.

---

## 1. Algorithm 1: Recursive Document Schema Inference

### Objective
Infer the observed schema profile from an arbitrary collection of BSON documents without enforcing a rigid static schema.

### Pseudocode
```
Algorithm: InferSchema(Collection C)
Input: MongoDB Collection C
Output: SchemaSnapshot S

1. Docs &larr; Fetch all documents from C
2. If |Docs| == 0 then:
       Return EmptySnapshot(fingerprint="empty_00000000")
3. Initialize PathStats &larr; Map<Path, StatObject>()

4. For each doc &isin; Docs do:
       SeenPathsInDoc &larr; Set()
       ExtractedTuples &larr; ExtractDocumentPaths(doc, prefix="")
       For each (path, value, type_name) &isin; ExtractedTuples do:
           If path &notin; PathStats then:
               PathStats[path] &larr; {occurrences: 0, nulls: 0, types: {}, samples: []}
           If path &notin; SeenPathsInDoc then:
               PathStats[path].occurrences &larr; PathStats[path].occurrences + 1
               SeenPathsInDoc.add(path)
           If value is None then:
               PathStats[path].nulls &larr; PathStats[path].nulls + 1
           PathStats[path].types[type_name] &larr; PathStats[path].types[type_name] + 1
           AppendSample(PathStats[path].samples, value)

5. FieldsProfile &larr; Map()
6. For each (path, stats) &isin; PathStats do:
       presence_ratio &larr; stats.occurrences / |Docs|
       missing_count  &larr; |Docs| - stats.occurrences
       non_null_types &larr; {t &isin; stats.types where t &ne; 'null'}
       is_mixed_type  &larr; |non_null_types| > 1
       FieldsProfile[path] &larr; {path, types: stats.types, presence_ratio, null_count: stats.nulls, missing_count, is_mixed_type, samples}

7. CanonicalRepresentation &larr; SortLexicographically(FieldsProfile)
8. Fingerprint &larr; SHA256(JSON(CanonicalRepresentation))[0:16]
9. Return SchemaSnapshot(id=GenerateID(), fingerprint=Fingerprint, fields=FieldsProfile)
```

---

## 2. Algorithm 2: Nested Path Traversal & Collision Detection

### Objective
Safely read, insert, and delete values at arbitrary dot-notated paths while detecting collisions with incompatible intermediate primitive types.

### Pseudocode
```
Algorithm: SetNestedPath(Document D, Path P, Value V)
Input: In-memory dictionary D, Dot-notated string P, Value V
Output: Modified Document D or Exception

1. Segments &larr; Split(P, ".")
2. CurrentNode &larr; D
3. For i &larr; 0 to |Segments| - 2 do:
       Key &larr; Segments[i]
       If Key &notin; CurrentNode then:
           CurrentNode[Key] &larr; EmptyDictionary()
       Else if Type(CurrentNode[Key]) is not Dictionary then:
           AncestorPath &larr; Join(Segments[0..i], ".")
           Raise PathStructureCollisionError(
               "Collision: intermediate path " + AncestorPath + 
               " is type " + TypeName(CurrentNode[Key]) + ", cannot nest."
           )
       CurrentNode &larr; CurrentNode[Key]

4. LeafKey &larr; Segments[|Segments| - 1]
5. CurrentNode[LeafKey] &larr; V
6. Return D
```

---

## 3. Algorithm 3: Controlled Schema Evolution with Compensating Rollback

### Objective
Apply a validated schema mutation to stored documents, persisting fine-grained provenance while safeguarding against partial failure.

### Pseudocode
```
Algorithm: ExecuteEvolution(Collection C, OpSpec Spec)
Input: Collection C, Operation Specification Spec
Output: OperationRecord Op, ProvenanceRecord Prov

1. ValidateOperationSpec(Spec)
2. PreSnapshot &larr; InferSchema(C)
3. Op &larr; CreateOperationRecord(Spec, pre_version=PreSnapshot.id, status="IN_PROGRESS")
4. Documents &larr; C.find({})
5. TransformedDocs &larr; []
6. FieldChanges &larr; []
7. AffectedDocIDs &larr; []

8. For each doc &isin; Documents do:
       (new_doc, change, outcome, conflict_msg) &larr; TransformDoc(doc, Spec)
       If outcome == "applied" then:
           Append(TransformedDocs, (doc._id, doc, new_doc))
           Append(FieldChanges, change)
           Append(AffectedDocIDs, doc._id)
       Else if outcome == "conflict" and not Spec.skip_conflicts then:
           Op.status &larr; "FAILED"
           Raise EvolutionValidationError(conflict_msg)

9. Journal &larr; []
10. Try:
        For each (doc_id, original_doc, new_doc) &isin; TransformedDocs do:
            C.replace_one({"_id": doc_id}, new_doc)
            Append(Journal, (doc_id, original_doc))
    Catch Exception E:
        // Compensating Rollback
        For each (doc_id, original_doc) &isin; Reverse(Journal) do:
            C.replace_one({"_id": doc_id}, original_doc)
        Op.status &larr; "FAILED"
        Op.error &larr; "Rolled back due to error: " + E.message
        Save(Op)
        Raise E

11. PostSnapshot &larr; InferSchema(C, operation_id=Op.id)
12. Prov &larr; CreateProvenanceRecord(Op.id, AffectedDocIDs, FieldChanges, pre_version=PreSnapshot.id, post_version=PostSnapshot.id)
13. Save(Prov)
14. Op.status &larr; "EXECUTED"
15. Op.post_version &larr; PostSnapshot.id
16. Save(Op)
17. Return (Op, Prov)
```

---

## 4. Algorithm 4: Static & Empirical Query Impact Analysis

### Objective
Identify potential breaking changes across registered application queries using both syntactic path intersection rules and empirical result verification.

### Pseudocode
```
Algorithm: AnalyzeImpact(Collection C, Operation Op)
Input: Target Collection C, Evolution Operation Op
Output: ImpactReport R

1. RegisteredQueries &larr; LoadEnabledQueries(C)
2. QueryAssessments &larr; []

3. For each query Q &isin; RegisteredQueries do:
       Severity &larr; "NONE"
       Classification &larr; "LIKELY_UNAFFECTED"
       Reasons &larr; []

       For each target_path &isin; [Op.source_path, Op.destination_path] do:
           For each q_path &isin; Q.referenced_paths do:
               // Case 1: Exact match
               If q_path == target_path then:
                   Severity &larr; "HIGH"
                   Classification &larr; "POTENTIALLY_AFFECTED"
                   Append(Reasons, "Direct modification of queried field '" + q_path + "'")

               // Case 2: Target is ancestor of query path
               Else if IsAncestor(target_path, q_path) then:
                   Severity &larr; "HIGH"
                   Classification &larr; "POTENTIALLY_AFFECTED"
                   Append(Reasons, "Parent structure containing '" + q_path + "' was modified/removed")

               // Case 3: Target is descendant of query path
               Else if IsDescendant(target_path, q_path) then:
                   If Severity != "HIGH" then:
                       Severity &larr; "MEDIUM"
                       Classification &larr; "POTENTIALLY_AFFECTED"
                   Append(Reasons, "Subfield inside composite object '" + q_path + "' was altered")

       // Empirical Verification
       CountBefore &larr; Q.last_result_count
       CountAfter  &larr; C.count_documents(Q.filter_spec)
       Delta &larr; CountAfter - CountBefore

       Append(QueryAssessments, {
           query_id: Q.id,
           classification: Classification,
           severity: Severity,
           reasons: Reasons,
           count_before: CountBefore,
           count_after: CountAfter,
           delta: Delta
       })

4. Return ImpactReport(assessments=QueryAssessments)
```

---

## 5. Algorithm 5: Conflict-Checked Recovery Inversion

### Objective
Invert an executed evolution operation back to its exact pre-operation state while preventing the silent destruction of subsequent user modifications.

### Pseudocode
```
Algorithm: SafeRecovery(OperationID OpID, Collection C)
Input: Identifier OpID, Collection C
Output: RecoveryResult Res

1. Prov &larr; FindProvenance(operation_id=OpID, is_recovery=false)
2. If HasBeenRecovered(OpID) then:
       Raise DuplicateRecoveryError("Operation already recovered.")

3. RestoredDocs &larr; []
4. ConflictingDocs &larr; []

5. For each change &isin; Prov.field_changes do:
       doc_id &larr; change.doc_id
       curr_doc &larr; C.find_one({"_id": doc_id})
       If curr_doc is None then:
           Append(ConflictingDocs, {doc_id, reason: "Document deleted downstream"})
           Continue

       inspect_path &larr; Prov.destination_path or Prov.source_path
       curr_val, curr_exists &larr; GetNestedPath(curr_doc, inspect_path)
       curr_fp &larr; ComputeFingerprint(curr_val, curr_exists)

       // Conflict Check: did post-operation state diverge?
       If curr_fp != change.post_state.fingerprint then:
           Append(ConflictingDocs, {
               doc_id, 
               reason: "Current fingerprint diverges from recorded post-operation state",
               current_value: curr_val,
               expected_value: change.post_state.value
           })
           Continue // Skip conflicting document to protect user edit

       // Construct inverse document
       restored_doc &larr; Copy(curr_doc)
       pre &larr; change.pre_state
       If not pre.exists then:
           DeleteNestedPath(restored_doc, inspect_path)
       Else if pre.is_null then:
           SetNestedPath(restored_doc, inspect_path, None)
       Else:
           SetNestedPath(restored_doc, inspect_path, pre.value)

       Append(RestoredDocs, (doc_id, restored_doc))

6. Apply updates to C for all RestoredDocs
7. RecoveryProv &larr; CreateRecoveryAudit(
       recovers_operation=OpID,
       restored_count=|RestoredDocs|,
       conflict_count=|ConflictingDocs|,
       conflicts=ConflictingDocs
   )
8. Save(RecoveryProv)
9. MarkOperation(OpID, status="RECOVERED")
10. Return {restored_count: |RestoredDocs|, conflict_count: |ConflictingDocs|}
```

---

## 6. Formal Metric Definitions

### 6.1 Document Impact Detection Metrics
Given ground-truth set of documents $G$ that should undergo mutation and detected set of mutated documents $D$:
- **True Positives ($TP$):** $TP = |G \cap D|$
- **False Positives ($FP$):** $FP = |D \setminus G|$
- **False Negatives ($FN$):** $FN = |G \setminus D|$

$$\text{Precision} = \frac{TP}{TP + FP} \quad (\text{if } TP+FP = 0, \text{ defined as } 1.0)$$

$$\text{Recall} = \frac{TP}{TP + FN} \quad (\text{if } TP+FN = 0, \text{ defined as } 1.0)$$

$$F_1\text{-Score} = \frac{2 \cdot \text{Precision} \cdot \text{Recall}}{\text{Precision} + \text{Recall}}$$

### 6.2 Query Impact Classification Accuracy
Given $M$ registered queries with ground-truth impact labels $Y_i \in \{\text{AFFECTED}, \text{UNAFFECTED}\}$ and predicted static classifications $\hat{Y}_i$:

$$\text{Accuracy}_{\text{query}} = \frac{1}{M} \sum_{i=1}^{M} \mathbb{I}(Y_i = \hat{Y}_i)$$

### 6.3 Recovery Correctness
Given $K$ restored documents, comparing each field $f$ against pre-operation ground truth $doc_{\text{pre}}$:

$$\text{Correctness}_{\text{recovery}} = \frac{|\{doc \in \text{Restored} \mid doc = doc_{\text{pre}}\}|}{|\text{Restored}|}$$

### 6.4 Provenance Storage Overhead
Let $B_{\text{prov}}$ be the total serialized JSON byte size of the provenance document and $N_{\text{affected}}$ be the number of affected documents:

$$\text{Overhead}_{\text{doc}} = \frac{B_{\text{prov}}}{N_{\text{affected}}} \quad (\text{bytes per affected document})$$
