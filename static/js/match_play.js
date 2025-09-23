let handicapRules = [];
let allParticipants = [];  // ← この行を fetchTodayParticipants の「前」に追加
let assignedParticipantIds = new Set();

// ✅ ページ読み込み時にクエリパラメータを取得する関数
function getQueryParam(key) {
  const params = new URLSearchParams(window.location.search);
  return params.get(key);
}

document.addEventListener("DOMContentLoaded", async () => { // HTML文書の読み込みが完了したときに実行する処理
  // JST（UTC+9）に補正した「今日」の日付文字列を作成
  const jstNow = new Date(Date.now() + 9 * 60 * 60 * 1000);
  const today = jstNow.toISOString().slice(0, 10);
  window.today = today;

  try {
    handicapRules = await fetchHandicapRules();

    // 🔽 ここだけ追加（URLから並び替えキーを取得）
    const sortKey = window.sortKey || "member_code";
    const sortOrder = window.sortOrder || "asc";

    // 🔄 並び替え指定付きで参加者を取得
    const participants = await fetchTodayParticipants(today, sortKey, sortOrder);
    window.participants = participants;
    const cards = await fetchMatchCards(today);

    await renderMatchCards(cards);
    renderParticipantTable(participants);

  } catch (error) {
    console.error("初期化中にエラー：", error);
  }

  // 🔽 本日2回目以上の対局モーダル用イベントバインド
  const repeatModal = document.getElementById("repeat-match-modal");
  const repeatContinueBtn = document.getElementById("repeat-match-continue");
  const repeatCancelBtn = document.getElementById("repeat-match-cancel");

  if (repeatContinueBtn) {
    repeatContinueBtn.addEventListener("click", () => {
      // モーダルを閉じるだけ（続行）
      repeatModal.style.display = "none";
      if (typeof window.repeatMatchContinueCallback === "function") {
        window.repeatMatchContinueCallback();
        window.repeatMatchContinueCallback = null;
      }
    });
  }

  if (repeatCancelBtn) {
    repeatCancelBtn.addEventListener("click", () => {
      // モーダルを閉じて手合い解除
      repeatModal.style.display = "none";
      if (typeof window.repeatMatchCancelCallback === "function") {
        window.repeatMatchCancelCallback();
        window.repeatMatchCancelCallback = null;
      }
    });
  }

  const endAllBtn = document.getElementById("end-all-button");
  const endAllModal = document.getElementById("end-all-modal");
  const endAllYes = document.getElementById("end-all-yes");
  const endAllNo = document.getElementById("end-all-no");

  if (endAllBtn && endAllModal && endAllYes && endAllNo) {
    endAllBtn.addEventListener("click", () => {
      endAllModal.style.display = "flex";
    });

    endAllNo.addEventListener("click", () => {
      endAllModal.style.display = "none";
    });

    endAllYes.addEventListener("click", async () => {
      try {
        const res = await fetch("/api/end_today", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ date: window.today }) // JSTずれが気になる場合はサーバ側でJST再計算
        });
        const data = await res.json();
        if (data.success) {
          alert("本日の参加者をリセットし、過去の対局カードを整理しました。");
          // 参加者編集へ戻る（仕様上ここに戻るのが自然）
          window.location.href = "/match/edit";
        } else {
          alert("処理に失敗しました: " + (data.message || ""));
        }
      } catch (e) {
        alert("通信エラーが発生しました。もう一度お試しください。");
      } finally {
        endAllModal.style.display = "none";
      }
    });
  }
});

async function fetchDefaultCardCount() {
  const res = await fetch("/api/default_card_count");
  const data = await res.json();
  return data.count || 5;
}

// ✅ 参加者一覧取得
async function fetchTodayParticipants(date, sort = "member_code", order = "asc") {
  const res = await fetch(`/api/participants?date=${date}&sort=${sort}&order=${order}`);
  const data = await res.json();
  allParticipants = data;
  return data;
}

// ✅ 駒落ちルール取得
async function fetchHandicapRules() {
  const res = await fetch("/api/handicap_rules");
  return await res.json(); // [{ grade_diff: 0, handicap: "平手" }, ...]
}

// ✅ ブラウザリロード時に対局カード状態取得　/api/match_card_state/load を呼び、cards配列を受け取る
async function fetchMatchCards(date) {
  const res = await fetch(`/api/match_card_state/load?date=${date}`);
  const data = await res.json();
  console.log("🔍 fetchMatchCards():", data);
  return data.cards || [];
}


// ✅ 参加者一覧描画（tbody部分）
function renderParticipantTable(participants) {
  const tbody = document.getElementById("participant-list");
  if (!tbody) return;

  tbody.innerHTML = participants
    .filter(p => !assignedParticipantIds.has(p.id))
    .map(p => `
      <tr draggable="true" ondragstart="drag(event)" id="participant-${p.id}">
        <td>${p.member_code ?? ""}</td>
        <td><a href="/member/${p.id}/recent" target="_blank" class="person-link">${p.name}</a></td>
        <td>${p.kana}</td>
        <td>${p.grade}</td>
        <td>${p.member_type}</td>
      </tr>
    `).join("");
}

// ✅ DBからロードしたカード状態をもとにHTMLを生成
// match_type に応じてセレクト状態を復元し、info_htmlやoriginal_htmlも復元
function renderMatchCards(cards) {
  const container = document.getElementById("cards-container");
  container.innerHTML = "";

  if (!cards || cards.length === 0) {
    // カード情報がない場合は default_card_count に従って空のカードを生成
    fetchDefaultCardCount().then(defaultCount => {
      const existingIndices = new Set((cards || []).map(c => c.card_index));
      for (let i = 0; i < defaultCount; i++) {
        if (!existingIndices.has(i)) {
          const emptyCard = createMatchCard(i);
          container.appendChild(emptyCard);
        }
      }
    });
    return;
  }

  // カードが存在する場合、保存された状態で描画
  assignedParticipantIds.clear(); // 初期化

  // 🔧 指導員は本日の参加者テーブルから消さないため、除外セットに入れない
  const addIfNonInstructor = (pid) => {
    if (!pid) return;
    const p = getParticipantDataById(pid);
    if (!p || p.member_type !== "指導員") {
      assignedParticipantIds.add(pid);
    }
  };

  cards.forEach(card => {
    addIfNonInstructor(card.p1_id);
    addIfNonInstructor(card.p2_id);

    const index = card.card_index;
    const cardElement = createMatchCard(index, card);
    container.appendChild(cardElement);


    // 要素取得（生成されたばかりの要素から取得）
    const cardDiv = document.getElementById(`match-card-${index}`);
    const infoDiv = document.getElementById(`match-info-${index}`);
    const p1 = document.getElementById(`card${index}-player1`);
    const p2 = document.getElementById(`card${index}-player2`);
    const startBtn = document.getElementById(`start-button-${index}`);
    const matchTypeSelect = document.getElementById(`match-type-${index}`);

    if (cardDiv) cardDiv.dataset.status = card.status || "";
    if (infoDiv) infoDiv.innerHTML = card.info_html || "";
    if (matchTypeSelect) matchTypeSelect.value = card.match_type || "認定戦";
    if (startBtn) startBtn.style.display = card.status === "ongoing" ? "none" : "inline-block";

    if (p1) {
      p1.dataset.participantId = card.p1_id || "";
      p1.dataset.originalHtml = card.original_html1 || "";
      if (card.original_html1) {
        p1.innerHTML = card.original_html1;
        p1.dataset.assigned = "true";
      }
    }
    if (p2) {
      p2.dataset.participantId = card.p2_id || "";
      p2.dataset.originalHtml = card.original_html2 || "";
      if (card.original_html2) {
        p2.innerHTML = card.original_html2;
        p2.dataset.assigned = "true";
      }
    }
    // 🔁 対局中のカードなら表示を復元
    if (card.status === "ongoing" && p1 && p2) {
      // 参加者情報を取得して名前等を抽出
      const id1 = card.p1_id;
      const id2 = card.p2_id;
      const p1data = getParticipantDataById(id1);
      const p2data = getParticipantDataById(id2);

      // ✅ 復元時も「開始時点」の棋力が未保持なら今の表示値で保持（なければ何もしない）
      const cardEl = document.getElementById(`match-card-${index}`);
      if (cardEl) {
        if (!cardEl.dataset.gradeAtTime1) cardEl.dataset.gradeAtTime1 = p1data?.grade || "";
        if (!cardEl.dataset.gradeAtTime2) cardEl.dataset.gradeAtTime2 = p2data?.grade || "";
      }      

      // 🔑 復元時も対局種別と未認定者フラグで正しく分岐
      const matchType = card.match_type || "認定戦";
      const isInitialAssessment = matchType === "初回認定";
      const isP1Unrated = p1data && p1data.grade === "未認定";
      const isP2Unrated = p2data && p2data.grade === "未認定";

      // 「◇（0.5勝）」を使うのは「初回認定戦」かつ「未認定者の相手側」だけ
      const p1UseHalf = isInitialAssessment && !isP1Unrated && isP2Unrated;
      const p2UseHalf = isInitialAssessment && !isP2Unrated && isP1Unrated;

      // p1NameHtml
      const p1NameHtml = `
        <div><strong>
          <a href="/member/${p1data.id}/recent" target="_blank" class="person-link">
            ${p1data.name}
          </a>
          （${p1data.kana}）${p1data.grade}・${p1data.member_type}
        </strong></div>
        <select id="result1-${index}" onchange="autoFillResult(${index}, 1)">
          <option value="">選択</option>
          ${p1UseHalf
            ? '<option value="◇">◇（0.5勝）</option>'
            : '<option value="○">○（勝）</option>'}
          ${p1UseHalf
            ? '<option value="◆">◆（ノーカウント負）</option>'
            : '<option value="●">●（負）</option>'}
          <option value="△">△（分）</option>
        </select>
      `;
      // p2NameHtml
      const p2NameHtml = `
        <div><strong>
          <a href="/member/${p2data.id}/recent" target="_blank" class="person-link">
            ${p2data.name}
          </a>
          （${p2data.kana}）${p2data.grade}・${p2data.member_type}
        </strong></div>
        <select id="result2-${index}" onchange="autoFillResult(${index}, 2)">
          <option value="">選択</option>
          ${p2UseHalf
            ? '<option value="◇">◇（0.5勝）</option>'
            : '<option value="○">○（勝）</option>'}
          ${p2UseHalf
            ? '<option value="◆">◆（ノーカウント負）</option>'
            : '<option value="●">●（負）</option>'}
          <option value="△">△（分）</option>
        </select>
      `;

      p1.innerHTML = p1NameHtml;
      p2.innerHTML = p2NameHtml;

      // 終了ボタンを復元
      const resultAreaId = `end-button-area-${index}`;
      if (!document.getElementById(resultAreaId)) {
        const endBtnArea = document.createElement("div");
        endBtnArea.id = resultAreaId;
        endBtnArea.innerHTML = `<button onclick="endMatch(${index})">対局終了</button>`;

        // ✅ ボタン共通エリアに追加
        const btnContainer = document.getElementById(`button-area-${index}`);
        if (btnContainer) {
          btnContainer.appendChild(endBtnArea);
        }
      }

      // 🔽 手合い解除ボタンも復元！
      const cancelBtnId = `cancel-button-${index}`;
      if (!document.getElementById(cancelBtnId)) {
        const cancelBtnDiv = document.createElement("div");
        cancelBtnDiv.id = cancelBtnId;
        cancelBtnDiv.style = "margin-right: 0.5rem;";
        cancelBtnDiv.innerHTML = `<button onclick="cancelMatch(${index})">手合い解除</button>`;

        const btnContainer = document.getElementById(`button-area-${index}`);
        if (btnContainer) {
          btnContainer.insertBefore(cancelBtnDiv, btnContainer.firstChild); // 左に表示
        }
      }

      // 🔽 指導対局なら「昇段級」ボタンを復元
      const buttonArea = document.getElementById(`button-area-${index}`);
      if (matchType === "指導" && buttonArea && !document.getElementById(`promote-button-${index}`)) {
        const promoteBtnDiv = document.createElement("div");
        promoteBtnDiv.id = `promote-button-${index}`;
        promoteBtnDiv.style = "margin-right: 0.5rem;";
        promoteBtnDiv.innerHTML = `<button onclick="showShodanModal(${index})">昇段級</button>`;
        buttonArea.insertBefore(promoteBtnDiv, buttonArea.firstChild);
      }

      // 🔽 初回認定戦なら「棋力認定」ボタンを復元
      if (matchType === "初回認定" && buttonArea && !document.getElementById(`shodan-button-${index}`)) {
        const shodanBtnDiv = document.createElement("div");
        shodanBtnDiv.id = `shodan-button-${index}`;
        shodanBtnDiv.style = "margin-right: 0.5rem;";
        shodanBtnDiv.innerHTML = `<button onclick="showShodanModal(${index})">棋力認定</button>`;
        buttonArea.insertBefore(shodanBtnDiv, buttonArea.firstChild);
      }
    }
  });

  // ★追加：保存枚数が既定枚数より少ないときは不足分を空カードで補充
  fetchDefaultCardCount().then(defaultCount => {
    const existingIndices = new Set(cards.map(c => c.card_index));
    for (let i = 0; i < defaultCount; i++) {
      if (!existingIndices.has(i)) {
        const emptyCard = createMatchCard(i);
        container.appendChild(emptyCard);
      }
    }
  });

}

// ✅ 新規カードの初期HTML構造を生成
function createMatchCard(index, card = null) {
  const div = document.createElement("div");
  div.className = "match-card";
  div.id = `match-card-${index}`;
  div.style = "border: 1px solid #ccc; padding: 1rem; margin-bottom: 1rem; background-color: #f9f9f9;";
  div.dataset.status = card?.status || "pending";

  if (div.dataset.status === "ongoing") {
    div.classList.add("in-progress");
    div.style.backgroundColor = "";   // ← inline背景を消してクラスの色を効かせる
  }
  div.innerHTML = `
    <strong>対局カード ${index + 1}</strong>

    <span style="float: right;">
      <button onclick="deleteCard(${index})">カード削除</button>
    </span>

    <div style="margin-top: 0.5rem;">
      <label>対局種別：
        <select onchange="onMatchTypeChange(this, ${index})" id="match-type-${index}">
          <option value="認定戦">棋力認定戦</option>
          <option value="指導">指導対局</option>
          <option value="フリー">フリー対局</option>
          <option value="初回認定">初回認定戦</option>
        </select>
      </label>
    </div>

  <div style="display: flex; gap: 1rem; margin-top: 0.5rem;">
    <div class="player-slot" ondrop="drop(event, 'player1', ${index})" ondragover="allowDrop(event)"
      id="card${index}-player1" data-assigned="false" style="border: 1px dashed #888; padding: 0.5rem; flex: 1; min-height: 40px;">
      対局者1
    </div>
    <div class="player-slot" ondrop="drop(event, 'player2', ${index})" ondragover="allowDrop(event)"
      id="card${index}-player2" data-assigned="false" style="border: 1px dashed #888; padding: 0.5rem; flex: 1; min-height: 40px;">
      対局者2
    </div>
  </div>

    <div id="match-info-${index}" style="margin-top: 0.5rem; color: #333;">
      ${card?.info_html || ""}
    </div>

  <div style="margin-top: 0.5rem; display: flex; justify-content: flex-end;" id="button-area-${index}">
    <div id="start-button-${index}" style="display: ${card?.status === "ongoing" ? "none" : "block"};">
      <button onclick="startMatch(${index})">対局開始</button>
    </div>
  </div>
  `;

  // 復元時の初期セット（任意）
  if (card?.p1_id) {
    div.dataset.player1Id = card.p1_id;
  }
  if (card?.p2_id) {
    div.dataset.player2Id = card.p2_id;
  }

  return div;
}

// 🔹 ドロップ可能にする
function allowDrop(ev) {
  ev.preventDefault();
}

// 🔹 ドラッグ開始
function drag(ev) {
  ev.dataTransfer.setData("text", ev.target.id);
}

async function drop(ev, slot, cardIndex) {
  ev.preventDefault();

  const draggedId = ev.dataTransfer.getData("text");
  const draggedElement = document.getElementById(draggedId);
  const slotId = `card${cardIndex}-${slot}`;
  const slotElement = document.getElementById(slotId);

  if (!draggedElement || !slotElement) return;

  if (slotElement.dataset.assigned === "true") {
    alert("すでに対局者が設定されています。");
    return;
  }

  // tr の id 属性から内部IDを取得（"participant-<id>"）
  const rowId = draggedElement.id || "";
  const id = rowId.startsWith("participant-") ? rowId.replace("participant-", "") : rowId;

  // 表示用データ：まずは最新キャッシュから取得（なければフォールバックでセル）
  const tds = draggedElement.querySelectorAll('td');
  if (tds.length < 5) {
    alert("データが正しく取得できませんでした。");
    return;
  }

  const p = getParticipantDataById(id); // ← 最新の参加者データ
  const memberCode = (p?.member_code ?? tds[0].innerText.trim());
  const name       = (p?.name        ?? tds[1].innerText.trim());
  const kana       = (p?.kana        ?? tds[2].innerText.trim());
  const grade      = (p?.grade       ?? tds[3].innerText.trim());
  const memberType = (p?.member_type ?? tds[4].innerText.trim());

  // 🔸 originalHtml を保存（キャンセル復元用）
  const originalHtml = draggedElement.innerHTML;
  slotElement.dataset.originalHtml = originalHtml;
  slotElement.dataset.participantRowId = draggedId; 

  // 🔸 表示変更（常に“いまの等級”を反映）
  slotElement.innerHTML = `
    <div><strong>${name}（${kana}）${grade}・${memberType}</strong></div>
    <button type="button" onclick="removeParticipant('${slot}', ${cardIndex}, '${draggedId}')">戻す</button>
  `;

  slotElement.dataset.assigned = "true";
  slotElement.dataset.participantId = id;

  // 🔸 指導員以外は参加者リストから非表示に
  if (memberType !== "指導員") {
    draggedElement.style.display = "none";
  }

  // 🔸 両者揃ったら「本日◯回目」チェック → 必要ならモーダル → 続行可なら showMatchInfo
  const p1 = document.getElementById(`card${cardIndex}-player1`);
  const p2 = document.getElementById(`card${cardIndex}-player2`);
  if (p1.dataset.assigned === "true" && p2.dataset.assigned === "true") {
    const proceed = await checkRepeatAndMaybeWarn(cardIndex);
    if (!proceed) {
      // 「手合い解除」が選ばれた場合はここで終了
      return;
    }
    await showMatchInfo(cardIndex);
  }
}

async function removeParticipant(slot, cardIndex, draggedId) {
  const slotId = `card${cardIndex}-${slot}`;
  const slotElement = document.getElementById(slotId);
  const draggedElement = document.getElementById(draggedId);
  const participantList = document.getElementById("participant-list");

  // 元に戻すHTMLが保存されている場合は復元
  const originalHtml = slotElement.dataset.originalHtml;
  const participantId = slotElement.dataset.participantId;
  const restoreRowId = draggedId || slotElement.dataset.participantRowId;

  if (participantId && originalHtml && !document.getElementById(restoreRowId)) {
    const newRow = document.createElement("tr");
    newRow.id = restoreRowId;
    newRow.setAttribute("draggable", "true");
    newRow.setAttribute("ondragstart", "drag(event)");
    // innerHTML（<td>…）だけを復元するので<tr>の二重化が起きない
    newRow.innerHTML = originalHtml;
    participantList.appendChild(newRow);
  } else if (draggedElement) {
    draggedElement.style.display = "";
  }

  // スロット初期化
  slotElement.innerHTML = slot === "player1" ? "対局者1" : "対局者2";
  slotElement.dataset.assigned = "false";
  slotElement.removeAttribute("data-participant-id");
  slotElement.removeAttribute("data-original-html");
  slotElement.removeAttribute("data-participant-row-id");

  // 対局情報も初期化
  const info = document.getElementById(`match-info-${cardIndex}`);
  if (info) info.innerHTML = "";

  const startBtn = document.getElementById(`start-button-${cardIndex}`);
  if (startBtn) startBtn.style.display = "none";

  await reloadParticipants();
}

function calcHandicap(order1, order2, matchType) {
  if (matchType === "指導") return "指導";
  if (matchType === "初回認定") return "認定";

  const diff = Math.abs(order1 - order2);
  const rule = handicapRules.find(r => r.grade_diff === diff);
  return rule ? rule.handicap : "平手";
}

// 両者揃ったときの駒落ち計算・勝てば昇段級メッセージの表示
// カード種別による駒落ち固定や◇（0.5勝）表示の条件もここ
async function showMatchInfo(cardIndex) { 
  // ★追加：直前の昇級を即時に反映するため最新の参加者一覧を再取得
  if (typeof reloadParticipants === "function") {
    await reloadParticipants();
  }

  const matchType = document.getElementById(`match-type-${cardIndex}`).value;

  const p1 = document.getElementById(`card${cardIndex}-player1`);
  const p2 = document.getElementById(`card${cardIndex}-player2`);
  const info = document.getElementById(`match-info-${cardIndex}`);
  const startBtn = document.getElementById(`start-button-${cardIndex}`);

  const id1 = p1.dataset.participantId;
  const id2 = p2.dataset.participantId;

  // 🔄 infoブロックを初期化（以前の駒落ち/ボタンを消す）
  info.innerHTML = "";
  startBtn.style.display = "none";

  // 対局者2名が揃っていなければ処理終了
  if (!id1 || !id2) return;

  const participant1 = getParticipantDataById(id1);
  const participant2 = getParticipantDataById(id2);

  const cardEl = document.getElementById(`match-card-${cardIndex}`);
  if (cardEl) {
    if (!cardEl.dataset.gradeAtTime1 && participant1) {
      cardEl.dataset.gradeAtTime1 = participant1.grade || "";
    }
    if (!cardEl.dataset.gradeAtTime2 && participant2) {
      cardEl.dataset.gradeAtTime2 = participant2.grade || "";
    }
  }

  if (matchType === "初回認定") {
    const grade1 = participant1.grade;
    const grade2 = participant2.grade;

    const isP1Unranked = grade1 === "未認定";
    const isP2Unranked = grade2 === "未認定";

    if (isP1Unranked && isP2Unranked) {
      alert("初回認定戦では、両者未認定は選べません（片方は認定者にしてください）");
      resetMatchCard(cardIndex);
      return;
    }

    if (!isP1Unranked && !isP2Unranked) {
      alert("初回認定戦では、片方が未認定者である必要があります");
      resetMatchCard(cardIndex);
      return;
    }
  }

  if (!participant1 || !participant2) {
    info.innerHTML = "棋力情報が取得できませんでした。";
    return;
  }

  // window.strengthOrderMap を優先し、なければ participant.grade_order を使う
  const toOrder = (p) => {
    if (!p) return 999;
    if (window.strengthOrderMap && p.grade in window.strengthOrderMap) {
      return window.strengthOrderMap[p.grade];
    }
    return (typeof p.grade_order === "number") ? p.grade_order : 999;
  };

  const order1 = toOrder(participant1);
  const order2 = toOrder(participant2);
  let handicap = calcHandicap(order1, order2, matchType);

  // 🔽 駒落ちセレクト：初回認定戦のみ固定、それ以外は変更可
  let handicapHtml = "";
  let options = [...new Set(handicapRules.map(r => r.handicap))];

  // 🔸 指導対局の場合、「指導」を先頭に追加
  if (matchType === "指導") {
    if (!options.includes("指導")) {
      options.unshift("指導");
    }
    handicap = "指導";
  }

  const handicapSelect = `
    <select id="handicap-select-${cardIndex}">
      ${options.map(opt => `
        <option value="${opt}" ${opt === handicap ? "selected" : ""}>${opt}</option>
      `).join("")}
    </select>
  `;

  if (matchType === "初回認定") {
    handicapHtml = `駒落ち：<strong>${handicap}</strong><input type="hidden" id="handicap-select-${cardIndex}" value="${handicap}">`;
  } else {
    handicapHtml = `駒落ち：${handicapSelect}`;
  }

  info.innerHTML = handicapHtml;
  startBtn.style.display = "block";

  // 🔽 勝てば昇段級の表示チェック（player1とplayer2両方）
  [participant1, participant2].forEach(async (p, i) => {
    if (!p || p.grade === "未認定") return;  // 未認定者は対象外

    // 次の勝ちが「0.5勝」かどうかだけを判定してサーバへ渡す（勝敗集計はサーバ側で一元管理）
    const other = (i === 0) ? participant2 : participant1;
    const nextWinIsHalf =
      (matchType === "初回認定") &&
      (p.grade !== "未認定") &&
      (other?.grade === "未認定");

    const checkRes = await fetch("/check_promotion", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        player_id: p.id,
        next_win_half: nextWinIsHalf
      })
    });

    const result = await checkRes.json();
    if (result?.success && result.promote) {
      const nextGrade = result.next_grade || "次段級";
      const msg = `${p.name} さんはこの対局に勝てば ${nextGrade} に昇段（級）します`;
      info.innerHTML += `<div style="color: red; margin-top: 0.3rem;">${msg}</div>`;
    }
  });

}

// 参加者IDから該当データを取得する
function getParticipantDataById(id) {
  return (
    window.participants?.find(p => p.id.toString() === id.toString()) ||
    allParticipants?.find(p => p.id.toString() === id.toString()) ||
    null
  );
}

// 対局開始時のUI切り替えと、カード種別ごとのボタン追加（棋力認定・昇段級・手合い解除）
// 対局情報（プレイヤー、駒落ち、種別など）をカードに表示する処理
async function startMatch(index) {  

  const startBtn = document.getElementById(`start-button-${index}`);
  if (startBtn) startBtn.style.display = "none";

  const card = document.getElementById(`match-card-${index}`);
  if (card) card.dataset.status = "ongoing";

  if (card) {
    card.classList.add("in-progress");   // ← 対局中の薄赤ハイライト
    card.style.backgroundColor = "";     // ★追加：インライン背景を解除してCSSを効かせる
  }

  // 対局種別と棋力を取得
  const matchType = document.getElementById(`match-type-${index}`).value;
  console.log("🟡 startMatch()：matchType =", matchType);

  // プレイヤー1
  const p1 = document.getElementById(`card${index}-player1`);
  const id1 = p1.dataset.participantId || "";
  const participant1 = getParticipantDataById(id1);

  // プレイヤー2
  const p2 = document.getElementById(`card${index}-player2`);
  const id2 = p2.dataset.participantId || "";
  const participant2 = getParticipantDataById(id2);

  // ✅ 対局「開始時点」の棋力をカード要素に保存（後で /save_match_result 送信に使う）
  const cardEl = document.getElementById(`match-card-${index}`);
  if (cardEl) {
    cardEl.dataset.gradeAtTime1 = participant1?.grade || "";
    cardEl.dataset.gradeAtTime2 = participant2?.grade || "";
  }

  // ここで判定！
  const isInitialAssessment = matchType === "初回認定";
  const isP1Unrated = participant1 && participant1.grade === "未認定";
  const isP2Unrated = participant2 && participant2.grade === "未認定";

    console.log("🟢 デバッグ情報：startMatch()", {
    matchType,
    isInitialAssessment,
    participant1: {
      id: id1,
      name: participant1?.name,
      grade: participant1?.grade
    },
    isP1Unrated,
    participant2: {
      id: id2,
      name: participant2?.name,
      grade: participant2?.grade
    },
    isP2Unrated
  });

  // プレイヤー1 表示
  const nameLink1 = participant1
    ? `<a href="/member/${participant1.id}/recent" target="_blank" class="person-link">${participant1.name}</a>`
    : "対局者1";
  const fullName1 = participant1
    ? `${nameLink1}（${participant1.kana}）${participant1.grade}・${participant1.member_type}`
    : "対局者1";

  const resultHTML1 = `
    <select id="result1-${index}" onchange="autoFillResult(${index}, 1)">
      <option value="">選択</option>
      ${isInitialAssessment && isP2Unrated && !isP1Unrated ? '<option value="◇">◇（0.5勝）</option>' : '<option value="○">○（勝）</option>'}
      ${isInitialAssessment && !isP1Unrated && isP2Unrated ? '<option value="◆">◆（ノーカウント負）</option>' : '<option value="●">●（負）</option>'}
      <option value="△">△（分）</option>
    </select>
  `;

  console.log("🎯 resultHTML1 =", resultHTML1);

  p1.innerHTML = `
    <div><strong>${fullName1}</strong></div>
    ${resultHTML1}
  `;

  // プレイヤー2 表示
  const nameLink2 = participant2
    ? `<a href="/member/${participant2.id}/recent" target="_blank" class="person-link">${participant2.name}</a>`
    : "対局者2";
  const fullName2 = participant2
    ? `${nameLink2}（${participant2.kana}）${participant2.grade}・${participant2.member_type}`
    : "対局者2";

  const resultHTML2 = `
    <select id="result2-${index}" onchange="autoFillResult(${index}, 2)">
      <option value="">選択</option>
      ${isInitialAssessment && isP1Unrated && !isP2Unrated ? '<option value="◇">◇（0.5勝）</option>' : '<option value="○">○（勝）</option>'}
      ${isInitialAssessment && !isP2Unrated && isP1Unrated ? '<option value="◆">◆（ノーカウント負）</option>' : '<option value="●">●（負）</option>'}
      <option value="△">△（分）</option>
    </select>
  `;

  p2.innerHTML = `
    <div><strong>${fullName2}</strong></div>
    ${resultHTML2}
  `;

  // 終了ボタンをカード下部に追加（なければ）
  const resultAreaId = `end-button-area-${index}`;
  if (!document.getElementById(resultAreaId)) {
    const endBtnArea = document.createElement("div");
    endBtnArea.id = resultAreaId;
    endBtnArea.innerHTML = `<button onclick="endMatch(${index})">対局終了</button>`;

    // ✅ ボタン共通エリアに追加
    const btnContainer = document.getElementById(`button-area-${index}`);
    if (btnContainer) {
      btnContainer.appendChild(endBtnArea);
    }
  }

  // 🔽 手合い解除ボタン追加（開始ボタンの左に）
  const buttonArea = document.getElementById(`button-area-${index}`);
  if (buttonArea && !document.getElementById(`cancel-button-${index}`)) {
    const cancelBtnDiv = document.createElement("div");
    cancelBtnDiv.id = `cancel-button-${index}`;
    cancelBtnDiv.style = "margin-right: 0.5rem;";
    cancelBtnDiv.innerHTML = `<button onclick="cancelMatch(${index})">手合い解除</button>`;
    buttonArea.insertBefore(cancelBtnDiv, buttonArea.firstChild);
  }

  // 🔽 指導対局の場合は「昇段級」ボタンを追加
  if (matchType === "指導" && buttonArea && !document.getElementById(`promote-button-${index}`)) {
    const promoteBtnDiv = document.createElement("div");
    promoteBtnDiv.id = `promote-button-${index}`;
    promoteBtnDiv.style = "margin-right: 0.5rem;";
    promoteBtnDiv.innerHTML = `<button onclick="showShodanModal(${index})">昇段級</button>`;
    buttonArea.insertBefore(promoteBtnDiv, buttonArea.firstChild); // 左に追加
  }

  // 🔽 初回認定戦の場合のみ、「棋力認定」ボタンを追加表示
  if (matchType === "初回認定" && buttonArea && !document.getElementById(`shodan-button-${index}`)) {
    const shodanBtnDiv = document.createElement("div");
    shodanBtnDiv.id = `shodan-button-${index}`;
    shodanBtnDiv.style = "margin-right: 0.5rem;";
    shodanBtnDiv.innerHTML = `<button onclick="showShodanModal(${index})">棋力認定</button>`;
    buttonArea.insertBefore(shodanBtnDiv, buttonArea.firstChild);  // ← 左側に追加
  }

  await saveAllMatchCardStates();
}

// 手合い解除・初期化の処理
async function resetMatchCard(index) {
  const card = document.getElementById(`match-card-${index}`);
  if (!card) return;

  // 対局カードのスロット初期化
  const p1 = document.getElementById(`card${index}-player1`);
  const p2 = document.getElementById(`card${index}-player2`);
  if (p1) {
    p1.innerHTML = "対局者1";
    p1.dataset.assigned = "false";
    p1.removeAttribute("data-participant-id");
    p1.removeAttribute("data-original-html");
    delete p1.dataset.gradeAtStart;   // ← 追加
  }
  if (p2) {
    p2.innerHTML = "対局者2";
    p2.dataset.assigned = "false";
    p2.removeAttribute("data-participant-id");
    p2.removeAttribute("data-original-html");
    delete p2.dataset.gradeAtStart;   // ← 追加
  }

  // 対局情報・ボタンなどを削除
  const info = document.getElementById(`match-info-${index}`);
  if (info) info.innerHTML = "";

  const endBtnArea = document.getElementById(`end-button-area-${index}`);
  if (endBtnArea) endBtnArea.remove();

  const cancelBtn = document.getElementById(`cancel-button-${index}`);
  if (cancelBtn) cancelBtn.remove();

  const promoteBtn = document.getElementById(`promote-button-${index}`);
  if (promoteBtn) promoteBtn.remove();

  const shodanBtn = document.getElementById(`shodan-button-${index}`);
  if (shodanBtn) shodanBtn.remove();

  // ステータス戻す＋ハイライト解除（★ ここで最初の card をそのまま使う）
  card.dataset.status = "pending";
  card.classList.remove("in-progress"); // CSSクラス方式の解除
  card.style.backgroundColor = "";      // 万一 inline で色を付けた場合の解除

  // 対局開始ボタン再表示
  const startBtn = document.getElementById(`start-button-${index}`);
  if (startBtn) startBtn.style.display = "block";

  // 対局種別を「認定戦」にリセット
  const matchTypeSelect = document.getElementById(`match-type-${index}`);
  if (matchTypeSelect) {
    matchTypeSelect.value = "認定戦";
    onMatchTypeChange(matchTypeSelect, index);
    fetch("/api/update_match_type", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index: index, match_type: "認定戦" })
    });
  }
  await reloadParticipants();
}

function autoFillResult(index, changedSide) {
  const r1 = document.getElementById(`result1-${index}`);
  const r2 = document.getElementById(`result2-${index}`);
  const val = changedSide === 1 ? r1.value : r2.value;

  // ★ 追加：特例判定に必要な情報を取得
  const matchType = getMatchTypeValue(index);
  const cardEl = document.getElementById(`match-card-${index}`);
  const gradeAtTime1 = cardEl?.dataset.gradeAtTime1 || "";
  const gradeAtTime2 = cardEl?.dataset.gradeAtTime2 || "";
  const isInitialAssessment = (matchType === "初回認定");
  const isP1Unrated = (gradeAtTime1 === "未認定");
  const isP2Unrated = (gradeAtTime2 === "未認定");

  if (val === "○" || val === "◇") {
    // 勝ち（○ / ◇）を選んだ側 → 通常は相手は負け
    // ★ 初回認定戦で「勝った側が未認定」かつ「相手が認定済」なら、相手は◆（ノーカウント負）
    if (changedSide === 1) {
      const loserShouldBeNoCount =
        isInitialAssessment && val === "○" && isP1Unrated && !isP2Unrated;
      r2.value = loserShouldBeNoCount ? "◆" : "●";
    } else {
      const loserShouldBeNoCount =
        isInitialAssessment && val === "○" && isP2Unrated && !isP1Unrated;
      r1.value = loserShouldBeNoCount ? "◆" : "●";
    }

  } else if (val === "◆") {
    // ノーカウント負（◆）を選んだ側 → 相手は必ず ○
    if (changedSide === 1) r2.value = "○";
    else r1.value = "○";

  } else if (val === "●") {
    // 通常の負け（●）
    // ★「初回認定」×「変更側が未認定」→ 相手は ◇（0.5勝）
    if (isInitialAssessment && (
          (changedSide === 1 && isP1Unrated) ||
          (changedSide === 2 && isP2Unrated)
        )) {
      if (changedSide === 1) r2.value = "◇";
      else r1.value = "◇";
    } else if (
      // ★「初回認定」×「変更側が認定済」×「相手が未認定」→ 誤入力の●は◆に置換し相手は○
      isInitialAssessment && (
        (changedSide === 1 && !isP1Unrated && isP2Unrated) ||
        (changedSide === 2 && !isP2Unrated && isP1Unrated)
      )
    ) {
      // 自分側を◆に修正して相手は○
      if (changedSide === 1) {
        r1.value = "◆";
        r2.value = "○";
      } else {
        r2.value = "◆";
        r1.value = "○";
      }
    } else {
      // それ以外は相手は ○
      if (changedSide === 1) r2.value = "○";
      else r1.value = "○";
    }

  } else if (val === "△") {
    r1.value = "△";
    r2.value = "△";

  } else {
    r1.value = "";
    r2.value = "";
  }
}


// 🔽 対局カードの状態をMatchCardStateに保存する
async function saveMatchCardState(index) {
  const today = window.today || new Date(Date.now() + 9 * 60 * 60 * 1000).toISOString().slice(0, 10);
  const p1 = document.getElementById(`card${index}-player1`);
  const p2 = document.getElementById(`card${index}-player2`);
  const info = document.getElementById(`match-info-${index}`);
  const matchType = document.getElementById(`match-type-${index}`).value;
  const status = document.getElementById(`match-card-${index}`).dataset.status || "";

  const payload = {
    date: today,
    cards: [  // ← 必ず cards: [] の中に入れること！
      {
        index: index,  // ← Flask側と一致させる
        match_type: matchType,
        p1_id: p1?.dataset.participantId || "",
        p2_id: p2?.dataset.participantId || "",
        status: status,
        info_html: info?.innerHTML || "",
        original_html1: p1?.dataset.originalHtml || "",
        original_html2: p2?.dataset.originalHtml || ""
      }
    ]
  };

  await fetch("/api/match_card_state/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
}

// 🔽 全ての対局カードの状態をMatchCardStateに保存する
async function saveAllMatchCardStates() {
  const today = window.today || new Date(Date.now() + 9 * 60 * 60 * 1000).toISOString().slice(0, 10);
  const cards = [];

  const allCards = document.querySelectorAll(".match-card");
  allCards.forEach(card => {
    const index = parseInt(card.id.replace("match-card-", ""));
    const p1 = document.getElementById(`card${index}-player1`);
    const p2 = document.getElementById(`card${index}-player2`);
    const info = document.getElementById(`match-info-${index}`);
    const matchType = document.getElementById(`match-type-${index}`).value;
    const status = card.dataset.status || "";

    cards.push({
      index,
      match_type: matchType,
      p1_id: p1?.dataset.participantId || "",
      p2_id: p2?.dataset.participantId || "",
      status,
      info_html: info?.innerHTML || "",
      original_html1: p1?.dataset.originalHtml || "",
      original_html2: p2?.dataset.originalHtml || ""
    });
  });

  await fetch("/api/match_card_state/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ date: today, cards })
  });
}

async function deleteMatchCardFromDB(index) {
  const today = window.today || new Date(Date.now() + 9 * 60 * 60 * 1000).toISOString().slice(0, 10);
  await fetch(`/api/match_card_state/delete?date=${today}&index=${index}`, {
    method: "DELETE",
  });
}

// 🔽 手合い解除処理
// /api/match_card_state/delete を呼び、画面側のHTMLをリセット。
async function cancelMatch(index) {
  const today = window.today || new Date(Date.now() + 9 * 60 * 60 * 1000).toISOString().slice(0, 10);

  // サーバーにDELETEリクエスト（p1/p2などを初期化）
  const res = await fetch(`/api/match_card_state/delete?date=${today}&index=${index}`, {
    method: "DELETE"
  });

  const data = await res.json();
  if (!data.success) {
    alert("手合い解除に失敗しました：" + data.message);
    return;
  }

  // 対局カードの状態を画面上でリセット
  const p1 = document.getElementById(`card${index}-player1`);
  const p2 = document.getElementById(`card${index}-player2`);
  const info = document.getElementById(`match-info-${index}`);
  const startBtn = document.getElementById(`start-button-${index}`);
  const endBtn = document.getElementById(`end-button-area-${index}`);
  const cancelBtn = document.getElementById(`cancel-button-${index}`);
  const card = document.getElementById(`match-card-${index}`);

  // 本日の参加者リストに戻す
  const id1 = p1?.dataset.participantId;
  const id2 = p2?.dataset.participantId;

  if (id1) removeParticipant("player1", index, `participant-${id1}`);
  if (id2) removeParticipant("player2", index, `participant-${id2}`);

  // スロット初期化
  if (p1) {
    p1.innerHTML = "対局者1";
    p1.dataset.participantId = "";
    p1.dataset.originalHtml = "";
    p1.removeAttribute("data-assigned");
  }
  if (p2) {
    p2.innerHTML = "対局者2";
    p2.dataset.participantId = "";
    p2.dataset.originalHtml = "";
    p2.removeAttribute("data-assigned");
  }

  // 表示初期化
  if (info) info.innerHTML = "";
  if (startBtn) startBtn.style.display = "block";
  if (endBtn) endBtn.remove();
  if (cancelBtn) cancelBtn.remove();

  // ✅ 追加：指導対局の「昇段級」ボタンも確実に消す
  const promoteBtn = document.getElementById(`promote-button-${index}`);
  if (promoteBtn) promoteBtn.remove();

  // ✅ 追加：初回認定戦の「棋力認定」ボタンも確実に消す
  const shodanBtn = document.getElementById(`shodan-button-${index}`);
  if (shodanBtn) shodanBtn.remove();

  if (card) {
    card.dataset.status = "pending";
    card.classList.remove("in-progress"); // 薄赤クラスを確実に外す
    card.style.backgroundColor = "";      // 念のためインライン色もクリア
  }

  // 🔽 対局種別を「認定戦」にリセット（画面上＋関連UI再構築＋サーバ更新）
  const matchTypeSelect = document.getElementById(`match-type-${index}`);
  if (matchTypeSelect) {
    matchTypeSelect.value = "認定戦";
    onMatchTypeChange(matchTypeSelect, index);   // ← 駒落ちや入力UIを認定戦仕様に戻す
    // best-effortでサーバ側にも反映（該当行が削除済みでもOK）
    fetch("/api/update_match_type", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index: index, match_type: "認定戦" })
    });
  }

  // 参加者リストを再表示（重複排除のため）
  await reloadParticipants();

}

function addMatchCard() {
  const container = document.getElementById("cards-container");
  const currentCards = document.querySelectorAll(".match-card");
  const newIndex = currentCards.length;

  const newCard = createMatchCard(newIndex);
  container.appendChild(newCard);

  // 状態保存
  saveMatchCardState(newIndex);
}

function deleteCard(index) {
  const card = document.getElementById(`match-card-${index}`);
  if (card) {
    card.remove();
  }
  deleteMatchCardFromDB(index);
}

// 🔽 追加：指導対局用モーダル表示（3択）
function showShidoModal(index, payload) {
  const modal = document.getElementById("shido-modal");
  modal.style.display = "flex"; // 中央表示

  // 「記録する」
  document.getElementById("shido-save").onclick = async () => {
    modal.style.display = "none";

    // ★追加：保存の前に、認定戦と同等の昇段級チェック＆確認→必要なら昇段級API
    await checkPromotionAndMaybePromote(index, payload);

    // その後に保存
    await actuallySaveMatch(index, payload);
  };

  // 「記録しない」
  document.getElementById("shido-norecord").onclick = async () => {
    modal.style.display = "none";
    await cancelMatch(index);
  };

  // 「キャンセル」
  document.getElementById("shido-cancel").onclick = () => {
    modal.style.display = "none";
    // 何もしない（対局終了自体をキャンセル）
  };
}

// 🔽 分離した保存処理
async function actuallySaveMatch(index, payload) { // 対局結果を保存する処理
  const res = await fetch("/save_match_result", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  const data = await res.json(); // サーバーからのレスポンスを取得
  if (data.success) {
    alert(data.message || "対局結果を記録しました。");
    removeParticipant("player1", index, `participant-${payload.player1_id}`);
    removeParticipant("player2", index, `participant-${payload.player2_id}`);
    resetMatchCard(index);
    await reloadParticipants();   // ★ ここを追加：保存後は必ず最新参加者を再取得
  } else {
    alert("保存に失敗しました：" + data.message);
  }
  const cancelBtn = document.getElementById(`cancel-button-${index}`);
  if (cancelBtn) cancelBtn.remove();
  await deleteMatchCardFromDB(index);
}

// ✅ 二重送信ガード：同じカード index の保存を同時に走らせない
const submittingMatches = new Set();

// 🔄 修正：endMatch を更新（昇段級処理と保存処理を関数「内」に収める）
async function endMatch(index) {
  // --- 二重送信ガード（最初にチェック） ---
  if (submittingMatches.has(index)) {
    // すでに送信中なら無視
    return;
  }
  submittingMatches.add(index);

  const card = document.getElementById(`match-card-${index}`);
  if (!card) {
    submittingMatches.delete(index);
    return;
  }

  const p1 = document.getElementById(`card${index}-player1`);
  const p2 = document.getElementById(`card${index}-player2`);
  const id1 = p1.dataset.participantId;
  const id2 = p2.dataset.participantId;

  const result1 = document.getElementById(`result1-${index}`).value;
  const result2 = document.getElementById(`result2-${index}`).value;
  const matchType = document.getElementById(`match-type-${index}`).value;
  const handicap = document.getElementById(`handicap-select-${index}`)?.value || "";

  const participant1 = getParticipantDataById(id1);
  const participant2 = getParticipantDataById(id2);

  const cardEl = document.getElementById(`match-card-${index}`);
  const gradeAtTime1 = cardEl?.dataset.gradeAtTime1 || "";
  const gradeAtTime2 = cardEl?.dataset.gradeAtTime2 || "";

  if (!result1 || !result2) {
    alert("勝敗を入力してください。");
    submittingMatches.delete(index);
    return;
  }

  const payload = {
    player1_id: id1,
    player2_id: id2,
    result1: result1,
    result2: result2,
    match_type: matchType,
    handicap: handicap,
    grade_at_time1: gradeAtTime1,
    grade_at_time2: gradeAtTime2,
    card_index: index,
    p1_opponent_grade: participant2?.grade || "",
    p2_opponent_grade: participant1?.grade || ""
  };

  // 指導対局はモーダルに委ねる（この場でロック解除して終了）
  if (matchType === "指導") {
    showShidoModal(index, payload);
    submittingMatches.delete(index); // モーダルに処理を委ねる
    return;
  }

  // 通常の対局終了処理前に昇段級チェック（勝った側のみ）
  const winners = [];
  if (result1 === "○" || result1 === "◇") winners.push({ id: id1, slot: "player1" });
  if (result2 === "○" || result2 === "◇") winners.push({ id: id2, slot: "player2" });

  let promoteHandled = false;

  try {
    // --- 昇段級：関数の外に出ていた処理を中に戻す ---
    for (const winner of winners) {
      const participant = getParticipantDataById(winner.id);
      if (!participant || participant.grade === "未認定") continue;

      // 相手情報から「次の勝ちが0.5勝か」を判定
      const opponentId = (winner.slot === "player1") ? id2 : id1;
      const opponent = getParticipantDataById(opponentId);
      const nextWinIsHalf =
        (matchType === "初回認定") &&
        (participant.grade !== "未認定") &&
        (opponent?.grade === "未認定");

      const checkRes = await fetch("/check_promotion", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          player_id: winner.id,
          next_win_half: nextWinIsHalf
        })
      });

      const result = await checkRes.json();
      if (result?.success && result.promote && result.next_grade) {
        const reasonText = result.reason ? `条件「${result.reason}」` : "昇段（級）条件";
        const confirmed = confirm(`${participant.name} は ${reasonText} を満たしました。\n${result.next_grade} に昇段（級）させますか？`);
        if (confirmed) {
          const res2 = await fetch("/api/promote_player", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              participant_id: winner.id,
              new_grade: result.next_grade,
              reason: result.reason || ""
            })
          });
          const pr = await res2.json();

          if (pr && pr.success) {
            const target = allParticipants.find(p => p.id.toString() === winner.id.toString());
            if (target) {
              target.grade = result.next_grade;
              if (window.strengthOrderMap) {
                target.grade_order = window.strengthOrderMap[result.next_grade] ?? -1;
              }
            }
            // 対局カードの「対局前棋力」も更新
            try {
              const cardEl2 = document.getElementById(`match-card-${index}`);
              if (cardEl2) {
                if (winner.slot === "player1") {
                  cardEl2.dataset.gradeAtTime1 = result.next_grade;
                } else {
                  cardEl2.dataset.gradeAtTime2 = result.next_grade;
                }
              }
            } catch (e) {
              console.warn("gradeAtTime の更新に失敗:", e);
            }
            await reloadParticipants();
            alert("昇段級処理を完了しました。");
          } else {
            alert("昇段級に失敗しました：" + (pr?.message || ""));
          }
          promoteHandled = true;
        }
      }
    }

    // --- 保存処理：これも関数の中に置く ---
    await actuallySaveMatch(index, payload);

  } finally {
    // 何があってもロック解除
    submittingMatches.delete(index);
  }
}

function showShodanModal(index) {
  const modal = document.getElementById("shodan-modal");
  const playerSelect = document.getElementById("shodan-player-select");
  const gradeSelect = document.getElementById("shodan-grade-select");

  // モーダル表示
  modal.style.display = "flex";

  // 🔧追加①：キャンセルボタンで閉じる（IDの揺れにも対応）
  const cancelBtn =
    document.getElementById("shodan-cancel") ||
    document.getElementById("shodan-close") ||
    document.querySelector('#shodan-modal [data-role="cancel"]');
  if (cancelBtn) {
    cancelBtn.onclick = () => {
      modal.style.display = "none";
      // 必要なら入力リセット
      // playerSelect.value = "";  // 運用により有効化
      // gradeSelect.selectedIndex = 0;
    };
  }

  // 🔧追加②：背景クリックで閉じる（多重バインド防止）
  if (!modal.dataset.bound) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) {
        modal.style.display = "none";
      }
    });
    modal.dataset.bound = "1";
  }

  // 🔧追加③：Escキーで閉じる（1回だけセット）
  if (!window.__shodanEscBound) {
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && modal.style.display !== "none") {
        modal.style.display = "none";
      }
    });
    window.__shodanEscBound = true;
  }

  // ▼ 対象カードのプレイヤー名でプルダウンの表示名を差し替える
  (function updateShodanPlayerSelect() {
    // カード上のスロット要素を取得
    const p1El = document.getElementById(`card${index}-player1`);
    const p2El = document.getElementById(`card${index}-player2`);
    const id1 = p1El?.dataset.participantId || "";
    const id2 = p2El?.dataset.participantId || "";

    // allParticipants から ID→名前を引く（なければスロットのテキストをフォールバック）
    const getNameById = (pid, fallback) => {
      try {
        const p = (typeof getParticipantDataById === "function")
          ? getParticipantDataById(pid)
          : (Array.isArray(window.allParticipants)
              ? window.allParticipants.find(x => x.id?.toString() === pid?.toString())
              : null);
        return (p && p.name) ? p.name : (fallback || "");
      } catch { return (fallback || ""); }
    };

    const name1 = id1 ? getNameById(id1, (p1El?.innerText || "").trim()) : "";
    const name2 = id2 ? getNameById(id2, (p2El?.innerText || "").trim()) : "";

    // プルダウンを作り直し（片方欠ける場合は片方のみ）
    playerSelect.innerHTML = "";
    if (id1) {
      const o1 = document.createElement("option");
      o1.value = "p1";                       // ← 既存処理が "p1"/"p2" を期待
      o1.textContent = `${name1}（対局者1）`;
      playerSelect.appendChild(o1);
    }
    if (id2) {
      const o2 = document.createElement("option");
      o2.value = "p2";
      o2.textContent = `${name2}（対局者2）`;
      playerSelect.appendChild(o2);
    }

    // どちらも空なら（カード未割当）安全側で初期化
    if (!id1 && !id2) {
      const o = document.createElement("option");
      o.value = "";
      o.textContent = "（対局者未割当）";
      playerSelect.appendChild(o);
    }

    // 1件しかない場合はそれを選択状態に
    if (playerSelect.options.length === 1) {
      playerSelect.selectedIndex = 0;
    }
  })();

  // 棋力プルダウンの中身を初期化して追加
  gradeSelect.innerHTML = "";
  const sortedGrades = Object.entries(window.strengthOrderMap)
    .sort((a, b) => a[1] - b[1])
    .map(([grade]) => grade);

  sortedGrades.forEach(grade => {
    const option = document.createElement("option");
    option.value = grade;
    option.textContent = grade;
    gradeSelect.appendChild(option);
  });

  // 昇段級（棋力認定）実行処理
  document.getElementById("shodan-confirm").onclick = async () => {
    const selectedPlayer = playerSelect.value; // "p1" or "p2"
    const newGrade = gradeSelect.value;

    const playerSlot = document.getElementById(`card${index}-player${selectedPlayer === "p1" ? 1 : 2}`);
    const participantId = playerSlot?.dataset.participantId;

    if (!participantId || !newGrade) {
      alert("対象者と新しい棋力を選んでください。");
      return;
    }

    // ✅ 対局種別から reason を決定（初回認定の記録を残すため）
    const matchType = document.getElementById(`match-type-${index}`)?.value || "";
    const reason = (matchType === "初回認定") ? "初回認定" : "指導";

    const res = await fetch("/api/promote_player", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ participant_id: participantId, new_grade: newGrade, reason })
    });

    const data = await res.json();
    if (data.success) {
      alert("昇段級処理を完了しました。");

      // 🔽 allParticipants の棋力を更新（UI反映）
      const target = allParticipants.find(p => p.id.toString() === participantId.toString());
      if (target) {
        target.grade = newGrade;
        if (window.strengthOrderMap) {
          target.grade_order = window.strengthOrderMap[newGrade] ?? -1;
        }
      }

      await reloadParticipants();

    } else {
      alert("昇段級に失敗しました：" + data.message);
    }

    modal.style.display = "none";
  };

}

async function reloadParticipants() {
  const today = window.today || new Date(Date.now() + 9 * 60 * 60 * 1000).toISOString().slice(0, 10);
  const participants = await fetchTodayParticipants(today);
  
  // 🔽 対局カードをスキャンして再構築
  assignedParticipantIds.clear();

  // 指導員は本日の参加者テーブルから消さない（カード復元時の方針と合わせる）
  const addIfNonInstructor = (pid) => {
    if (!pid) return;
    const pdata = getParticipantDataById(pid); // allParticipants / window.participants を参照
    if (!pdata || pdata.member_type !== "指導員") {
      assignedParticipantIds.add(pid);
    }
  };

  const cards = document.querySelectorAll(".match-card");
  cards.forEach(card => {
    const idx = card.id.replace("match-card-", "");
    const p1 = document.getElementById(`card${idx}-player1`);
    const p2 = document.getElementById(`card${idx}-player2`);
    addIfNonInstructor(p1?.dataset.participantId);
    addIfNonInstructor(p2?.dataset.participantId);
  });
  renderParticipantTable(participants);
}

// 🔽 並び替え処理（APIを呼び直して描画）
async function sortParticipants(key) {
  const url = new URL(window.location.href);
  const currentSort = url.searchParams.get("sort") || "id";
  const currentOrder = url.searchParams.get("order") || "asc";

  // 昇順⇔降順の切替
  const newOrder = (currentSort === key && currentOrder === "asc") ? "desc" : "asc";

  url.searchParams.set("sort", key);
  url.searchParams.set("order", newOrder);

  // URLだけ更新（履歴残さず）
  window.history.replaceState(null, "", url);

  // 並び替えたデータを再取得して描画
  const today = window.today;
  const sorted = await fetch(`/api/participants?date=${today}&sort=${key}&order=${newOrder}`);
  const data = await sorted.json();
  allParticipants = data; // 上書き
  renderParticipantTable(data);
}

function onMatchTypeChange(select, index) {
  console.log(`対局種別が変更されました：カード${index} →`, select.value);
}

function getMatchTypeValue(cardIndex) {
  const sel = document.getElementById(`match-type-${cardIndex}`);
  return sel ? sel.value : "認定戦";
}

// 本日認定系の同一ペア回数を問い合わせ、必要ならモーダル/確認を出す
async function checkRepeatAndMaybeWarn(cardIndex) {
  try {
    const matchType = getMatchTypeValue(cardIndex);
    // 対象は「認定戦」「初回認定」のみ
    if (!["認定戦", "初回認定"].includes(matchType)) return true;

    const p1 = document.getElementById(`card${cardIndex}-player1`);
    const p2 = document.getElementById(`card${cardIndex}-player2`);
    const id1 = p1?.dataset.participantId;
    const id2 = p2?.dataset.participantId;
    if (!id1 || !id2) return true;

    // サーバに回数確認（JST当日、認定系・記録済のみを集計）
    const res = await fetch(`/api/today_pair_count?p1=${encodeURIComponent(id1)}&p2=${encodeURIComponent(id2)}`);
    const data = await res.json();

    if (!data?.success) return true;

    const count = Number(data.count || 0);
    if (count < 1) return true; // 初対局ならそのまま進行

    // 2回目以上 → モーダル（なければ confirm にフォールバック）
    const nth = count + 1; // 1→2回目、2→3回目…
    const modal = document.getElementById("repeat-warning-modal");

    if (modal) {
      return await openRepeatWarningModal(modal, nth, cardIndex);
    } else {
      const ok = window.confirm(`本日${nth}回目の対局です。\n「OK」で続行、「キャンセル」で手合い解除します。`);
      if (!ok) {
        await resetMatchCard(cardIndex); // 参加者に戻す
        return false;
      }
      return true;
    }
  } catch (e) {
    console.error("checkRepeatAndMaybeWarn error:", e);
    // 失敗時は安全側（続行）に倒す
    return true;
  }
}

// モーダルで「それでも対局する / 手合い解除」を選ばせる
function openRepeatWarningModal(modal, nth, cardIndex) {
  return new Promise((resolve) => {
    // 文言差し込み
    const msgEl = modal.querySelector("[data-role='repeat-message']");
    if (msgEl) msgEl.textContent = `本日${nth}回目の対局です`;

    // 表示
    modal.style.display = "flex";

    // ハンドラ（多重登録防止のため一旦既存をクリア）
    const proceedBtn = modal.querySelector("[data-action='proceed']");
    const cancelBtn  = modal.querySelector("[data-action='cancel']");
    const closeModal = () => { modal.style.display = "none"; };

    // 既存のonclickを消してから新しく割り当て
    if (proceedBtn) proceedBtn.onclick = null;
    if (cancelBtn)  cancelBtn.onclick  = null;

    if (proceedBtn) {
      proceedBtn.onclick = () => {
        closeModal();
        resolve(true);   // 続行
      };
    }
    if (cancelBtn) {
      cancelBtn.onclick = async () => {
        closeModal();
        await resetMatchCard(cardIndex); // 手合い解除
        resolve(false);
      };
    }

    // 背景クリックで閉じる → 手合い解除はしない（誤操作防止）
    if (!modal.dataset.repeatBound) {
      modal.addEventListener("click", (e) => {
        if (e.target === modal) {
          closeModal();
          resolve(true); // 背景クリックは続行扱い
        }
      });
      modal.dataset.repeatBound = "1";
    }
  });
}

// ★追加：保存前に昇段級チェック＆確認ポップアップ＆昇段級APIを実行する共通関数
async function checkPromotionAndMaybePromote(index, payload) {
  const { player1_id: id1, player2_id: id2, result1, result2 } = payload;
  const matchType = document.getElementById(`match-type-${index}`)?.value || "";

  // 勝者抽出（○/◇のみ対象）
  const winners = [];
  if (result1 === "○" || result1 === "◇") winners.push({ id: id1, slot: "player1" });
  if (result2 === "○" || result2 === "◇") winners.push({ id: id2, slot: "player2" });

  for (const winner of winners) {
    const participant = getParticipantDataById(winner.id);
    if (!participant || participant.grade === "未認定") continue;

    // 相手情報と「次の勝ちが0.5勝か」を判定（初回認定のみ該当）
    const opponentId = (winner.slot === "player1") ? id2 : id1;
    const opponent = getParticipantDataById(opponentId);
    const nextWinIsHalf =
      (matchType === "初回認定") &&
      (participant.grade !== "未認定") &&
      (opponent?.grade === "未認定");

    // サーバに「次の1勝で昇段級か？」を問い合わせ
    const resp = await fetch("/check_promotion", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        player_id: winner.id,
        next_win_half: nextWinIsHalf
      })
    });
    const result = await resp.json();

    if (result?.success && result.promote && result.next_grade) {
      const reasonText = result.reason ? `条件「${result.reason}」` : "昇段（級）条件";
      const confirmed = confirm(`${participant.name} は ${reasonText} を満たしました。\n${result.next_grade} に昇段（級）させますか？`);
      if (confirmed) {
        const res2 = await fetch("/api/promote_player", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            participant_id: winner.id,
            new_grade: result.next_grade,
            reason: result.reason || ""
          })
        });
        const pr = await res2.json();
        if (pr && pr.success) {
          // 画面上の棋力も更新
          const target = allParticipants.find(p => p.id.toString() === winner.id.toString());
          if (target) {
            target.grade = result.next_grade;
            if (window.strengthOrderMap) {
              target.grade_order = window.strengthOrderMap[result.next_grade] ?? -1;
            }
          }
          // 対局カードの「対局前棋力」表示も更新しておく（続く保存でgrade_at_timeは別管理）
          try {
            const cardEl2 = document.getElementById(`match-card-${index}`);
            if (cardEl2) {
              if (winner.slot === "player1") {
                cardEl2.dataset.gradeAtTime1 = result.next_grade;
              } else {
                cardEl2.dataset.gradeAtTime2 = result.next_grade;
              }
            }
          } catch (e) {
            console.warn("gradeAtTime の更新に失敗:", e);
          }
          await reloadParticipants();
          alert("昇段級処理を完了しました。");
        } else {
          alert("昇段級に失敗しました：" + (pr?.message || ""));
        }
      }
    }
  }
}
