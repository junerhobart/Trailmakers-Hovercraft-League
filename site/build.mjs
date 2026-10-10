import { copyFile, mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const siteDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectDirectory = path.resolve(siteDirectory, "..");
const rulesDirectory = path.join(projectDirectory, "rules", "soccer");
const outputDirectory = path.resolve(projectDirectory, process.argv[2] ?? "dist");

const escapeHtml = (value) => value
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const renderInline = (value) => escapeHtml(value)
  .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
  .replace(/`(.+?)`/g, "<code>$1</code>");

function renderMarkdown(markdown) {
  const output = [];
  let listOpen = false;

  const closeList = () => {
    if (listOpen) {
      output.push("</ul>");
      listOpen = false;
    }
  };

  for (const rawLine of markdown.split("\n")) {
    const line = rawLine.trim();

    if (!line) {
      closeList();
      continue;
    }

    if (line.startsWith("- ")) {
      if (!listOpen) {
        output.push("<ul>");
        listOpen = true;
      }
      output.push(`<li>${renderInline(line.slice(2))}</li>`);
      continue;
    }

    closeList();
    const heading = line.match(/^(#{1,4})\s+(.+)$/);

    if (heading) {
      const level = Math.min(6, heading[1].length + 2);
      output.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
    } else {
      output.push(`<p>${renderInline(line)}</p>`);
    }
  }

  closeList();
  return output.join("\n");
}

const categoryOrder = ["gameplay", "weapons", "penalties"];
const entries = await readdir(rulesDirectory, { withFileTypes: true });
const categories = entries
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name)
  .sort((a, b) => {
    const aIndex = categoryOrder.indexOf(a);
    const bIndex = categoryOrder.indexOf(b);
    return (aIndex < 0 ? categoryOrder.length : aIndex)
      - (bIndex < 0 ? categoryOrder.length : bIndex)
      || a.localeCompare(b);
  });

if (categories.length === 0) {
  throw new Error(`No rule categories found in ${rulesDirectory}`);
}

const navigation = [];
const sections = [];

for (const category of categories) {
  const categoryDirectory = path.join(rulesDirectory, category);
  const files = (await readdir(categoryDirectory))
    .filter((file) => file.endsWith(".md"))
    .sort((a, b) => {
      if (a === "rules.md") return -1;
      if (b === "rules.md") return 1;
      return a.localeCompare(b);
    });

  if (files.length === 0) continue;

  const id = category
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  const label = category
    .split("-")
    .filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(" ");
  const rules = await Promise.all(files.map(async (file) => {
    const markdown = await readFile(path.join(categoryDirectory, file), "utf8");
    return renderMarkdown(markdown);
  }));

  navigation.push(`<a href="#${id}">${escapeHtml(label)}</a>`);
  sections.push(`<section id="${id}">\n<h2>${escapeHtml(label)}</h2>\n${rules.join("\n")}\n</section>`);
}

if (sections.length === 0) {
  throw new Error(`No Markdown rule files found in ${rulesDirectory}`);
}

const template = await readFile(path.join(siteDirectory, "index.html"), "utf8");
const page = template
  .replace("{{NAVIGATION}}", navigation.join("\n        "))
  .replace("{{RULES}}", sections.join("\n      "));

await mkdir(outputDirectory, { recursive: true });
await writeFile(path.join(outputDirectory, "index.html"), page);
await copyFile(path.join(siteDirectory, "styles.css"), path.join(outputDirectory, "styles.css"));
