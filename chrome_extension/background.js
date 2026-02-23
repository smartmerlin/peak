const AGENT_URL = "http://localhost:7834/chrome-event";

// URL blocklist — loaded from storage, editable via popup
let urlBlocklist = [];

chrome.storage.local.get("urlBlocklist", (result) => {
  if (result.urlBlocklist) {
    urlBlocklist = result.urlBlocklist;
  }
});

chrome.storage.onChanged.addListener((changes) => {
  if (changes.urlBlocklist) {
    urlBlocklist = changes.urlBlocklist.newValue || [];
  }
});

function isBlocked(url) {
  if (!url) return false;
  return urlBlocklist.some((pattern) => url.includes(pattern));
}

function sendEvent(tabTitle, url) {
  if (isBlocked(url)) {
    tabTitle = "[blocked]";
    url = "[blocked]";
  }

  fetch(AGENT_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      timestamp: new Date().toISOString(),
      tab_title: tabTitle || "",
      url: url || "",
    }),
  }).catch(() => {
    // Agent not running — silently ignore
  });
}

// Tab activated (switched to a different tab)
chrome.tabs.onActivated.addListener((activeInfo) => {
  chrome.tabs.get(activeInfo.tabId, (tab) => {
    if (chrome.runtime.lastError || !tab) return;
    sendEvent(tab.title, tab.url);
  });
});

// Window focus changed
chrome.windows.onFocusChanged.addListener((windowId) => {
  if (windowId === chrome.windows.WINDOW_ID_NONE) return;
  chrome.tabs.query({ active: true, windowId }, (tabs) => {
    if (chrome.runtime.lastError || !tabs || tabs.length === 0) return;
    sendEvent(tabs[0].title, tabs[0].url);
  });
});

// Tab updated (navigation within a tab)
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  // Only fire when the URL actually changes on the active tab
  if (!changeInfo.url) return;
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (chrome.runtime.lastError || !tabs || tabs.length === 0) return;
    if (tabs[0].id === tabId) {
      sendEvent(tab.title, changeInfo.url);
    }
  });
});
