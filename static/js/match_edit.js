document.addEventListener("DOMContentLoaded", () => {

  function syncLeftMembers(participantsList) {
    try {
      const idSet = new Set((participantsList || []).map(p => String(p.id)));
      document.querySelectorAll('tbody#members-table tr[data-id]').forEach(tr => {
        const mid = tr.dataset.id;
        tr.style.display = idSet.has(String(mid)) ? 'none' : '';
      });
    } catch (e) {
      console.warn('syncLeftMembers error:', e);
    }
  }
  
  // ローカル日付で YYYY-MM-DD を安定取得
  const today = new Date(Date.now() - (new Date()).getTimezoneOffset() * 60000)
                  .toISOString().slice(0, 10);
  const participantsTable = document.getElementById("participants-table");
  const proceedBtn = document.getElementById("proceed-button");
  const addBtn = document.getElementById("add-button");
  const sort = getQueryParam("sort_participants") || "member_code";
  const order = getQueryParam("order_participants") || "asc";
  const repeatModal = document.getElementById("repeat-match-modal");
  const repeatContinueBtn = document.getElementById("repeat-match-continue");
  const repeatCancelBtn = document.getElementById("repeat-match-cancel");
  
  // この画面（参加者編集）には repeat 系モーダルが無いので、存在チェックしてからバインド
  if (repeatModal && repeatContinueBtn && repeatCancelBtn) {
    // 「それでも対局する」
    repeatContinueBtn.addEventListener("click", () => {
      repeatModal.style.display = "none";
      if (typeof window.repeatMatchCallback === "function") {
        window.repeatMatchCallback(true); // 続行
      }
    });

    // 「手合い解除」
    repeatCancelBtn.addEventListener("click", () => {
      repeatModal.style.display = "none";
      if (typeof window.repeatMatchCallback === "function") {
        window.repeatMatchCallback(false); // 手合い解除
      }
    });
  }

  // 初期読み込み：参加者一覧取得
  fetch(`/api/participants?date=${today}&sort=${sort}&order=${order}&_=${Date.now()}`, {
    cache: 'no-store',
    headers: { 'Cache-Control': 'no-cache' }
  })
    .then(res => res.json())
    .then(data => {
      sortParticipants(data); // 並び替え適用

      while (participantsTable.firstChild) {
        participantsTable.removeChild(participantsTable.firstChild);
      }

      data.forEach(p => appendParticipantRow(p));
      // ★変更：共通関数で左表同期
      syncLeftMembers(data);
    });

  async function reloadParticipants() {
    const res = await fetch(`/api/participants?date=${today}&sort=${sort}&order=${order}&_=${Date.now()}`, {
      cache: 'no-store',
      headers: { 'Cache-Control': 'no-cache' }
    });
    const list = await res.json();
    sortParticipants(list);

    while (participantsTable.firstChild) {
      participantsTable.removeChild(participantsTable.firstChild);
    }
    list.forEach(p => appendParticipantRow(p));
    syncLeftMembers(list);
    toggleProceedButton();
  }

  // 「参加受付」ボタン押下時
  addBtn.addEventListener("click", () => {
    const checkedBoxes = document.querySelectorAll(".member-check:checked");
    const ids = Array.from(checkedBoxes).map(cb => {
      const row = cb.closest("tr");
      return row.dataset.id;
    });

    if (ids.length === 0) {
      alert("会員を選択してください。");
      return;
    }

    fetch("/api/participants", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ date: today, ids: ids })
    })
    .then(res => res.json())
    .then(data => {
      if (data.success) {
        ids.forEach(id => {
          const row = document.querySelector(`tbody#members-table tr[data-id="${id}"]`);
          if (row) {
            // 削除ではなく非表示にする（取消で復活できるように）
            row.style.display = 'none';
            // 念のためチェックも外す
            const cb = row.querySelector('.member-check');
            if (cb) cb.checked = false;
          }
        });

        // 送信済みチェックは念のため全解除
        document.querySelectorAll(".member-check:checked").forEach(cb => cb.checked = false);

        // 右リストは必ず再取得→並べ替え→全描画で一貫性を担保
        reloadParticipants();
      } else {
        alert("登録に失敗しました");
      }
    });
  });

  // 参加者行を追加する関数
  function appendParticipantRow(p) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${p.member_code ?? ""}</td>
      <td>${p.name}</td>
      <td>${p.kana}</td>
      <td>${p.grade}</td>
      <td>${p.member_type}</td>
      <td><button data-id="${p.id}" class="btn btn-outline remove-btn">取消</button></td>
    `;
    participantsTable.appendChild(tr);

    toggleProceedButton();
  }

  // 進行ボタンの有効化切替
  function toggleProceedButton() {
    const hasParticipants = participantsTable.children.length > 0;

    if (hasParticipants) {
      proceedBtn.style.pointerEvents = "auto";
      proceedBtn.style.opacity = "1";
    } else {
      proceedBtn.style.pointerEvents = "none";
      proceedBtn.style.opacity = "0.5";
    }
  }

   // 「対局進行」ボタン押下時
  proceedBtn.addEventListener("click", () => {
    const rows = participantsTable.querySelectorAll("tr");
    const ids = Array.from(rows).map(row => {
      const btn = row.querySelector("button");
      return btn?.dataset.id;
    });

    fetch("/set_today_participants", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: ids })
    })
      .then(res => res.json())
      .then(data => {
        if (data.success) {
          window.location.href = "/match/play";
        } else {
          alert("参加者の保存に失敗しました。");
        }
      });
  }); 

  // 参加者表（右）のイベント委任
  participantsTable.addEventListener("click", async (e) => {
    const btn = e.target.closest(".remove-btn");
    if (!btn) return;

    const id = btn.dataset.id;
    const tr = btn.closest("tr");

    // 二重押下防止＆操作中表示
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "…";

    try {
      const res = await fetch(`/api/participants/${id}?date=${today}`, {
        method: "DELETE",
        headers: { "Cache-Control": "no-cache" }
      });

      // 対局中（409） or APIが in_match=true を返した場合はポップアップ表示して中断
      let data = {};
      try { data = await res.json(); } catch (_) {}

      if (res.status === 409 || data?.in_match) {
        alert(data?.message || "対局中のため取り消せません。");
        btn.disabled = false;
        btn.textContent = originalText;
        return; // ← リロードしない（取消しない）
      }

      // 通常成功時は従来どおり即リロード
      if (data?.success) {
        setTimeout(() => location.reload(), 50);
        return;
      }

      // それ以外（失敗）もメッセを出して復帰
      alert(data?.message || "削除に失敗しました。");
      btn.disabled = false;
      btn.textContent = originalText;

    } catch (err) {
      // 通信エラー時
      alert("通信エラーが発生しました。");
      btn.disabled = false;
      btn.textContent = originalText;
    }
  });

});

// URLパラメータを取得するユーティリティ関数
function getQueryParam(name) {
  const url = new URL(window.location.href);
  return url.searchParams.get(name);
}

// 参加者を並び替える
function sortParticipants(participants) {
  const sortKey = getQueryParam('sort_participants') || 'member_code';
  const order = getQueryParam('order_participants') || 'asc';

  const customOrder = {
    "正会員": 1,
    "臨時会員": 2,
    "指導員": 3,
    "スタッフ": 4
  };

  const codeKey = (code) => {
    const s = String(code ?? "");
    const isNum = /^[0-9]+$/.test(s);
    return [!isNum ? 1 : 0, isNum ? parseInt(s, 10) : 0, s]; // 0=数値,1=英字混じり
  };

  const cmpTuple = (ka, kb) => {
    for (let i = 0; i < Math.max(ka.length, kb.length); i++) {
      const a = ka[i] ?? 0;
      const b = kb[i] ?? 0;
      if (a < b) return -1;
      if (a > b) return 1;
    }
    return 0;
  };

  participants.sort((a, b) => {
    if (sortKey === 'grade') {
      const va = a.grade_order ?? 999;
      const vb = b.grade_order ?? 999;
      return (order === 'asc') ? (va - vb) : (vb - va);
    }
    if (sortKey === 'member_type') {
      const va = customOrder[a.member_type] || 99;
      const vb = customOrder[b.member_type] || 99;
      return (order === 'asc') ? (va - vb) : (vb - va);
    }
    if (sortKey === 'member_code') {
      const ka = codeKey(a.member_code);
      const kb = codeKey(b.member_code);
      const base = cmpTuple(ka, kb);
      return (order === 'asc') ? base : -base;
    }

    // name / kana などは日本語ロケール比較
    const va = String(a[sortKey] ?? "");
    const vb = String(b[sortKey] ?? "");
    const cmp = va.localeCompare(vb, "ja");
    return order === 'asc' ? cmp : -cmp;
  });

}
