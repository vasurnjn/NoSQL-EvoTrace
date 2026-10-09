/**
 * NoSQL-EvoTrace Frontend UI Script
 */

document.addEventListener("DOMContentLoaded", function () {
  // 1. Auto-dismiss flash alerts on click
  const alerts = document.querySelectorAll(".alert");
  alerts.forEach(function (alert) {
    alert.style.cursor = "pointer";
    alert.addEventListener("click", function () {
      alert.style.transition = "opacity 0.3s ease";
      alert.style.opacity = "0";
      setTimeout(function () {
        alert.remove();
      }, 300);
    });
  });

  // 2. Evolution Workspace Form Controls
  const opSelect = document.getElementById("operationTypeSelect");
  if (opSelect) {
    const srcGroup = document.getElementById("sourcePathGroup");
    const dstGroup = document.getElementById("destinationPathGroup");
    const valGroup = document.getElementById("defaultValueGroup");
    const typeGroup = document.getElementById("targetTypeGroup");
    const opHint = document.getElementById("operationHintText");

    const hints = {
      add_field: "Inserts a new field with a default value across documents where it is missing. Example: Destination <code>account_tier</code>, Default <code>standard</code> (String).",
      delete_field: "Safely removes an existing field path from documents. Example: Source <code>notes</code> or <code>legacy_flag</code>.",
      rename_field: "Renames an existing key within the same scope. Example: Source <code>postal_code</code> ➔ Destination <code>zip_code</code>.",
      move_field: "Restructures a field between flat and nested scopes. Example: Source <code>city</code> ➔ Destination <code>address.city</code>.",
      convert_type: "Safely casts values to the target type without data loss. Example: Source <code>age</code> ➔ Target Type <code>integer</code>."
    };

    function updateFormFields() {
      const selected = opSelect.value;
      if (opHint && hints[selected]) {
        opHint.innerHTML = hints[selected];
      }
      if (selected === "add_field") {
        if (srcGroup) srcGroup.style.display = "none";
        if (dstGroup) dstGroup.style.display = "block";
        if (valGroup) valGroup.style.display = "block";
        if (typeGroup) typeGroup.style.display = "none";
      } else if (selected === "delete_field") {
        if (srcGroup) srcGroup.style.display = "block";
        if (dstGroup) dstGroup.style.display = "none";
        if (valGroup) valGroup.style.display = "none";
        if (typeGroup) typeGroup.style.display = "none";
      } else if (selected === "rename_field" || selected === "move_field") {
        if (srcGroup) srcGroup.style.display = "block";
        if (dstGroup) dstGroup.style.display = "block";
        if (valGroup) valGroup.style.display = "none";
        if (typeGroup) typeGroup.style.display = "none";
      } else if (selected === "convert_type") {
        if (srcGroup) srcGroup.style.display = "block";
        if (dstGroup) dstGroup.style.display = "none";
        if (valGroup) valGroup.style.display = "none";
        if (typeGroup) typeGroup.style.display = "block";
      }
    }

    window.setEvolutionExample = function(opType, src, dst) {
      opSelect.value = opType;
      updateFormFields();
      const srcInput = document.getElementById("sourcePathInput");
      const dstInput = document.getElementById("destinationPathInput");
      if (srcInput && src) srcInput.value = src;
      if (dstInput && dst) dstInput.value = dst;
    };

    opSelect.addEventListener("change", updateFormFields);
    updateFormFields();

    // Dry Run Preview Button Handler
    const previewBtn = document.getElementById("previewEvolutionBtn");
    const previewContainer = document.getElementById("previewResultsArea");

    if (previewBtn && previewContainer) {
      previewBtn.addEventListener("click", function () {
        previewBtn.disabled = true;
        previewBtn.innerText = "Simulating Dry-Run…";

        const form = document.getElementById("evolutionForm");
        const formData = new FormData(form);
        const payload = Object.fromEntries(formData.entries());

        fetch("/evolution/preview", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": payload.csrf_token || "",
          },
          body: JSON.stringify(payload),
        })
          .then((res) => res.json())
          .then((data) => {
            previewBtn.disabled = false;
            previewBtn.innerText = "Preview Dry-Run";

            if (!data.success) {
              previewContainer.innerHTML = `
                <div class="alert alert-error">
                  <strong>Preview Validation Error:</strong> ${data.error}
                </div>
              `;
              previewContainer.style.display = "block";
              return;
            }

            const p = data.preview;
            let conflictHtml = "";
            if (p.conflicts && p.conflicts.length > 0) {
              conflictHtml = `
                <div class="warning-callout">
                  <strong>⚠️ Conflicts / Structural Warnings (${p.conflict_count}):</strong>
                  <ul style="margin-left: 1.25rem; margin-top: 0.25rem;">
                    ${p.conflicts.map((c) => `<li>Doc <code>${c.doc_id}</code>: ${c.reason}</li>`).join("")}
                  </ul>
                </div>
              `;
            }

            let samplesHtml = "";
            if (p.sample_previews && p.sample_previews.length > 0) {
              samplesHtml = `
                <div style="margin-top: 1rem;">
                  <h4 style="font-size: 0.9rem; font-weight: 600; margin-bottom: 0.5rem;">Sample Document Transformations:</h4>
                  ${p.sample_previews
                    .map(
                      (s) => `
                    <div class="diff-box">
                      <div><strong>Doc ID:</strong> <code>${s.doc_id}</code> (Status: <span class="badge badge-success">${s.outcome}</span>)</div>
                      <div style="margin-top: 0.25rem;">
                        <span class="diff-pre">PRE: [Exists: ${s.pre_state.exists}, Type: ${s.pre_state.type}, Val: ${JSON.stringify(s.pre_state.value)}]</span>
                        &nbsp;➔&nbsp;
                        <span class="diff-post">POST: [Exists: ${s.post_state.exists}, Type: ${s.post_state.type}, Val: ${JSON.stringify(s.post_state.value)}]</span>
                      </div>
                    </div>
                  `
                    )
                    .join("")}
                </div>
              `;
            }

            let queryImpactHtml = "";
            if (p.impact_summary) {
              queryImpactHtml = `
                <div style="margin-top: 1rem; padding: 0.75rem; background: #fff; border: 1px solid var(--border-color); border-radius: var(--radius-md);">
                  <div style="font-weight: 600; font-size: 0.85rem; margin-bottom: 0.35rem;">Registered Query Impact:</div>
                  <div style="font-size: 0.8rem; color: var(--text-muted);">
                    Evaluated <strong>${p.impact_summary.total_queries}</strong> registered queries:
                    <span class="badge ${p.impact_summary.affected_queries > 0 ? "badge-danger" : "badge-success"}">
                      ${p.impact_summary.affected_queries} potentially affected
                    </span>
                  </div>
                </div>
              `;
            }

            previewContainer.innerHTML = `
              <div class="preview-card">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                  <h3 style="font-size: 1rem; font-weight: 700;">Dry-Run Simulation Results</h3>
                  <span class="badge ${p.can_execute_cleanly ? "badge-success" : "badge-warning"}">
                    ${p.can_execute_cleanly ? "Clean Execution Possible" : "Warnings / Conflicts Detected"}
                  </span>
                </div>
                <div class="grid-cards" style="margin: 1rem 0 0.5rem 0;">
                  <div class="card" style="padding: 0.75rem;">
                    <div class="card-title">Total Docs</div>
                    <div class="card-value" style="font-size: 1.25rem;">${p.total_documents}</div>
                  </div>
                  <div class="card" style="padding: 0.75rem;">
                    <div class="card-title">Will Mutate</div>
                    <div class="card-value" style="font-size: 1.25rem; color: var(--success);">${p.affected_count}</div>
                  </div>
                  <div class="card" style="padding: 0.75rem;">
                    <div class="card-title">Skipped</div>
                    <div class="card-value" style="font-size: 1.25rem; color: var(--text-muted);">${p.skipped_count}</div>
                  </div>
                  <div class="card" style="padding: 0.75rem;">
                    <div class="card-title">Conflicts</div>
                    <div class="card-value" style="font-size: 1.25rem; color: var(--danger);">${p.conflict_count}</div>
                  </div>
                </div>
                ${conflictHtml}
                ${queryImpactHtml}
                ${samplesHtml}
              </div>
            `;
            previewContainer.style.display = "block";
          })
          .catch((err) => {
            previewBtn.disabled = false;
            previewBtn.innerText = "Preview Dry-Run";
            previewContainer.innerHTML = `<div class="alert alert-error">Network error during dry-run simulation: ${err}</div>`;
            previewContainer.style.display = "block";
          });
      });
    }
  }
});
