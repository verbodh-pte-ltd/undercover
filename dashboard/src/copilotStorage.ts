// What the Copilot's sidebar keeps in this browser: whether it is open, and each handler's open
// conversation. A browser that keeps nothing, such as one in private mode, starts afresh.
const OPEN = "undercover-copilot-open";
const CONVERSATION = "undercover-copilot-conversation";

function read(key: string) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // Nothing is kept, so the sidebar starts afresh next time.
  }
}

// Open the first time, then as the handler left it.
export const savedOpen = () => read(OPEN) !== "closed";
export const saveOpen = (open: boolean) => {
  write(OPEN, open ? "open" : "closed");
};

export const savedConversation = (handlerId: string) =>
  read(`${CONVERSATION}-${handlerId}`);
export const saveConversation = (handlerId: string, id: string | null) => {
  write(`${CONVERSATION}-${handlerId}`, id);
};
