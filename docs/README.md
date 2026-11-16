# pf-tool Documentation

[![Built with Starlight](https://astro.badg.es/v2/built-with-starlight/tiny.svg)](https://starlight.astro.build)

AC/DC power-flow solver for the Brazilian Interconnected Power System (BIPS).

## 📖 Documentation

The complete documentation is available at:
- **Live Site:** https://mcllerena.github.io/pf-tool/
- **Source:** [`src/content/docs/`](src/content/docs/)

## 🚀 Building Documentation Locally

```bash
npm install
npm run dev      # Local preview at http://localhost:4321
npm run build    # Build static site to ./dist/
npm run preview  # Preview built site locally
```

## 📁 Project Structure

```
.
├── src/
│   ├── assets/
│   ├── content/
│   │   └── docs/           # Documentation pages
│   └── content.config.ts
├── astro.config.mjs        # Starlight configuration
├── package.json
└── tsconfig.json
```

## 📝 Content Organization

Documentation is organized in `src/content/docs/`:

- **Getting Started** - Installation and basic usage
- **Data Blocks** - ANAREDE `.dat` file format documentation
- **Components** - Bus, branch, transformer, SVC, HVDC, and shunt models
- **Methods** - Newton-Raphson and optimization solvers
- **Exporting** - Excel workbook export functionality

See the [main README](../README.md) for package information.

