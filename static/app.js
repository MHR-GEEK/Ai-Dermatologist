const loginForm = document.querySelector("#loginForm");
const brandForm = document.querySelector("#brandForm");
const scanForm = document.querySelector("#scanForm");
const imageInput = document.querySelector("#imageInput");
const preview = document.querySelector("#preview");
const adminMessage = document.querySelector("#adminMessage");
const scanMessage = document.querySelector("#scanMessage");
const brandList = document.querySelector("#brandList");
const resultBox = document.querySelector("#result");
const chatForm = document.querySelector("#chatForm");
const chatInput = document.querySelector("#chatInput");
const chatLog = document.querySelector("#chatLog");
const chatMessage = document.querySelector("#chatMessage");
const adminToggle = document.querySelector("#adminToggle");
const adminPanel = document.querySelector("#adminPanel");

function setMessage(node, message, isError = false) {
  node.textContent = message;
  node.classList.toggle("error", isError);
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    throw new Error(data.message || `Request failed (HTTP ${response.status})`);
  }
  return data;
}

function renderBrands(brands) {
  brandList.replaceChildren();
  brands.forEach((brand) => {
    const button = document.createElement("button");
    button.className = "chip";
    button.type = "button";
    button.dataset.brand = brand;
    button.textContent = `${brand} x`;
    brandList.appendChild(button);
  });
}

function safeText(value) {
  return String(value ?? "-").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[character]);
}

function renderList(items) {
  return items.map((item) => `<li>${safeText(item)}</li>`).join("") || "<li>-</li>";
}

function renderResult(result) {
  const concerns = Array.isArray(result.concerns) ? result.concerns : [];
  const routine = Array.isArray(result.routine) ? result.routine : [];
  const proportions = Array.isArray(result.proportion_notes) ? result.proportion_notes : [];

  resultBox.className = "result has-result";
  resultBox.innerHTML = `
    <div class="facts">
      <div><span>Skin Type</span><strong>${safeText(result.skin_type)}</strong></div>
      <div><span>Sensitivity</span><strong>${safeText(result.sensitivity)}</strong></div>
      <div><span>AI Status</span><strong>${result.ai_available ? "Active" : "Not configured"}</strong></div>
    </div>
    <div class="score-grid auto-scale">
      <div class="score-card"><span>PSL Style Scale</span><strong>${safeText(result.psl_score)}</strong><small>Subjective visual harmony, not identity or worth.</small></div>
      <div class="score-card"><span>Facial Rating</span><strong>${safeText(result.facial_rating)}</strong><small>Aesthetic impression from this image only.</small></div>
      <div class="score-card"><span>Image Ratio Score</span><strong>${safeText(result.image_ratio_score)}</strong><small>Photo angle, facial thirds, symmetry, lighting.</small></div>
    </div>
    <h3>Proportion Notes</h3><ul>${renderList(proportions)}</ul>
    <h3>Visible Concerns</h3><ul>${renderList(concerns)}</ul>
    <h3>Routine</h3>
    <div class="routine">${routine.map((item) => `<div><span>${safeText(item.step || "Step")}</span><p>${safeText(item.recommendation)}</p></div>`).join("") || "<p>-</p>"}</div>
    <p class="notes">${safeText(result.notes || "")}</p>
  `;
}

adminToggle.addEventListener("click", () => {
  adminPanel.hidden = !adminPanel.hidden;
});

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await requestJson("/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: document.querySelector("#password").value }),
    });
    setMessage(adminMessage, data.message);
  } catch (error) {
    setMessage(adminMessage, error.message, true);
  }
});

brandForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.querySelector("#brandInput");
  try {
    const data = await requestJson("/admin/brands", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ brand: input.value }),
    });
    input.value = "";
    renderBrands(data.brands);
    setMessage(adminMessage, "Brand list updated");
  } catch (error) {
    setMessage(adminMessage, error.message, true);
  }
});

brandList.addEventListener("click", async (event) => {
  const button = event.target.closest(".chip");
  if (!button) return;
  try {
    const response = await fetch(`/admin/brands/${encodeURIComponent(button.dataset.brand)}`, { method: "DELETE" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) throw new Error(data.message || "Could not remove brand");
    renderBrands(data.brands);
    setMessage(adminMessage, "Brand removed");
  } catch (error) {
    setMessage(adminMessage, error.message, true);
  }
});

imageInput.addEventListener("change", () => {
  const file = imageInput.files[0];
  if (!file) return;
  preview.src = URL.createObjectURL(file);
  preview.alt = file.name;
  preview.style.display = "block";
  setMessage(scanMessage, file.name);
});

scanForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = imageInput.files[0];
  if (!file) {
    setMessage(scanMessage, "Upload a face image first", true);
    return;
  }
  const formData = new FormData();
  formData.append("image", file);
  const button = scanForm.querySelector('button[type="submit"]');
  button.disabled = true;
  setMessage(scanMessage, "Analyzing image...");
  try {
    const data = await requestJson("/analyze", { method: "POST", body: formData });
    renderResult(data.result);
    setMessage(scanMessage, "Analysis complete");
  } catch (error) {
    setMessage(scanMessage, error.message, true);
  } finally {
    button.disabled = false;
  }
});

function addChatBubble(role, text) {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}`;
  bubble.textContent = text;
  chatLog.appendChild(bubble);
  chatLog.scrollTop = chatLog.scrollHeight;
}

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = chatInput.value.trim();
  if (!message) {
    setMessage(chatMessage, "Type a message first", true);
    return;
  }

  addChatBubble("user", message);
  chatInput.value = "";
  const sendButton = chatForm.querySelector('button[type="submit"]');
  sendButton.disabled = true;
  setMessage(chatMessage, "Assistant is thinking...");
  try {
    const data = await requestJson("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    addChatBubble("assistant", data.reply);
    setMessage(chatMessage, "");
  } catch (error) {
    setMessage(chatMessage, error.message, true);
    addChatBubble("assistant", `I couldn't answer that: ${error.message}`);
  } finally {
    sendButton.disabled = false;
    chatInput.focus();
  }
});
