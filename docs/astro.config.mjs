// @ts-check
import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { unified } from "@astrojs/markdown-remark";

// https://astro.build/config
export default defineConfig({
	markdown: {
		// LaTeX math (`$…$`, `$$…$$`) rendered with KaTeX.
		processor: unified({ remarkPlugins: [remarkMath], rehypePlugins: [rehypeKatex] }),
	},
	integrations: [
		starlight({
			title: "BIPS AC/DC Power Flow",
			description:
				"AC/DC power flow for the Brazilian Interconnected Power System (BIPS): ANAREDE .dat data model, component handling, Newton-Raphson and optimization power flow.",
			customCss: ["katex/dist/katex.min.css", "./src/styles/custom.css"],
			sidebar: [
				{
					label: "Start here",
					items: [
						{ label: "Overview", link: "/" },
						{ label: "Getting started", slug: "getting-started" },
						{ label: "Solution pipeline", slug: "pipeline" },
						{ label: "Exporting results", slug: "export" },
					],
				},
				{
					label: "Data file",
					items: [
						{ label: "Data blocks", slug: "data/blocks" },
						{ label: "Block columns", slug: "data/columns" },
						{ label: "Options and constants", slug: "data/options" },
					],
				},
				{
					label: "Components",
					items: [
						{ label: "Buses and generators", slug: "components/buses" },
						{ label: "Lines and transformers", slug: "components/branches" },
						{ label: "Tap and phase controls", slug: "components/transformer-controls" },
						{ label: "Series compensators (CSC)", slug: "components/csc" },
						{ label: "Shunt banks", slug: "components/shunts" },
						{ label: "Static VAR compensators", slug: "components/svc" },
						{ label: "HVDC LCC links", slug: "components/hvdc-lcc" },
						{ label: "Network reduction", slug: "components/network" },
					],
				},
				{
					label: "Methods",
					items: [
						{ label: "Newton-Raphson", slug: "methods/newton-raphson" },
						{ label: "Optimization power flow", slug: "methods/optimization" },
						{ label: "Results and benchmarks", slug: "methods/results" },
					],
				},
			],
		}),
	],
});
