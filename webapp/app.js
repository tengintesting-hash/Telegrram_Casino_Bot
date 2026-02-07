const tg = window.Telegram?.WebApp;
const user = tg?.initDataUnsafe?.user;

const state = {
  telegramId: user?.id || null,
  username: user?.username || null,
};

const appEl = document.getElementById("app");
const subscriptionBlock = document.getElementById("subscription-block");
const channelLinks = document.getElementById("channel-links");
const tasksContainer = document.getElementById("tasks");
const profileInfo = document.getElementById("profile-info");
const newsList = document.getElementById("news-list");
const supportButton = document.getElementById("support-button");

function show(element) {
  element.classList.remove("hidden");
}

function hide(element) {
  element.classList.add("hidden");
}

function setActivePage(pageId) {
  document.querySelectorAll(".page").forEach((page) => {
    page.classList.toggle("active", page.id === pageId);
  });
}

async function validateSubscription() {
  const response = await fetch("/api/validate-subscription", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      telegram_id: state.telegramId,
      username: state.username,
    }),
  });
  if (!response.ok) {
    throw new Error("Subscription validation failed");
  }
  const data = await response.json();
  return data.missing || [];
}

function renderChannels(channels) {
  channelLinks.innerHTML = "";
  channels.forEach((channel) => {
    const link = document.createElement("a");
    link.href = channel.channel_username
      ? `https://t.me/${channel.channel_username}`
      : `https://t.me/c/${channel.channel_id}`;
    link.textContent = channel.channel_title || channel.channel_id;
    link.target = "_blank";
    channelLinks.appendChild(link);
  });
}

async function loadTasks() {
  const response = await fetch(`/api/tasks?telegram_id=${state.telegramId}`);
  const data = await response.json();
  tasksContainer.innerHTML = "";
  data.tasks.forEach((task) => {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <h3>${task.title}</h3>
      <p>${task.description || ""}</p>
      <p>Type: ${task.task_type} | Rarity: ${task.rarity}</p>
      <p>Reward: ${task.reward_tokens} PRO#</p>
      <p>Status: ${task.status || "pending"}</p>
      <button ${task.status === "completed" ? "disabled" : ""}>Mark Complete</button>
    `;
    const button = card.querySelector("button");
    button.addEventListener("click", async () => {
      await fetch("/api/tasks/complete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ telegram_id: state.telegramId, task_id: task.id }),
      });
      loadTasks();
    });
    tasksContainer.appendChild(card);
  });
}

async function loadProfile() {
  const response = await fetch(`/api/profile?telegram_id=${state.telegramId}`);
  const data = await response.json();
  profileInfo.innerHTML = `
    <p>Telegram ID: ${data.telegram_id}</p>
    <p>Username: ${data.username || "-"}</p>
    <p>Referral link: <a href="${data.referral_link}" target="_blank">${data.referral_link}</a></p>
    <p>Tokens: ${data.tokens}</p>
    <p>Token rate: ${data.token_rate}</p>
  `;
  supportButton.onclick = () => window.open(data.support_link, "_blank");
}

async function loadNews() {
  const response = await fetch("/api/news");
  const data = await response.json();
  newsList.innerHTML = "";
  data.news.forEach((item) => {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <h3>${item.title}</h3>
      <p>${item.content || ""}</p>
      ${item.media_url ? `<p>${item.media_type}: <a href="${item.media_url}" target="_blank">View</a></p>` : ""}
      ${item.button_url ? `<a class="btn" href="${item.button_url}" target="_blank">${item.button_text || "Open"}</a>` : ""}
    `;
    newsList.appendChild(card);
  });
}

async function init() {
  if (!state.telegramId) {
    subscriptionBlock.innerHTML = "<p>Open this WebApp from Telegram.</p>";
    show(subscriptionBlock);
    return;
  }
  const missing = await validateSubscription();
  if (missing.length) {
    renderChannels(missing);
    show(subscriptionBlock);
    hide(appEl);
    return;
  }
  hide(subscriptionBlock);
  show(appEl);
  await Promise.all([loadTasks(), loadProfile(), loadNews()]);
}

document.querySelectorAll(".bottom-nav button").forEach((button) => {
  button.addEventListener("click", () => {
    setActivePage(button.dataset.page);
  });
});

init();
