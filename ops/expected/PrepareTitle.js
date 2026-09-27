/*
 * Bounded QuickAdd title normalizer for the KnowledgeOS mobile-safe surface.
 *
 * This helper only normalizes a supplied title. It does not execute shell
 * commands, call a provider, read files, or write the Vault. QuickAdd can use
 * the returned value when a later, explicitly audited plugin profile is active.
 */
module.exports = async ({ variables = {} } = {}) => {
  const raw = variables.TITLE ?? variables.title ?? "";
  return String(raw)
    .normalize("NFC")
    .replace(/[\\/:*?"<>|\u0000-\u001f\u007f]/g, "-")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 120);
};

