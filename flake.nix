{
  description = "Trailmark source-code graph analysis toolkit";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
      ...
    }:
    let
      inherit (nixpkgs) lib;
      systems = [
        "aarch64-darwin"
        "x86_64-linux"
      ];
      forAllSystems = lib.genAttrs systems;

      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };

      overlay = workspace.mkPyprojectOverlay {
        sourcePreference = "wheel";
      };

      pyproject = builtins.fromTOML (builtins.readFile ./pyproject.toml);

      treeSitterCli =
        pkgs:
        let
          version = (builtins.fromJSON (builtins.readFile ./package.json)).devDependencies.tree-sitter-cli;
          assets = {
            x86_64-linux = {
              platform = "linux-x64";
              sha256 = "ad69bfbd54f9ebba1387bf0e7c2683896a5968f2f78768380af70f0c4dd8ceab";
            };
            aarch64-darwin = {
              platform = "macos-arm64";
              sha256 = "b914b20b5e6e706e571600476cf27c3bfe57aa3e431ccc944ec52b6c47766e9f";
            };
          };
          asset = assets.${pkgs.stdenv.hostPlatform.system};
        in
        pkgs.stdenv.mkDerivation {
          pname = "trailmark-tree-sitter-cli";
          inherit version;
          src = pkgs.fetchurl {
            url = "https://github.com/tree-sitter/tree-sitter/releases/download/v${version}/tree-sitter-${asset.platform}.gz";
            inherit (asset) sha256;
          };
          nativeBuildInputs = [
            pkgs.gzip
          ]
          ++ lib.optionals pkgs.stdenv.hostPlatform.isLinux [ pkgs.autoPatchelfHook ];
          buildInputs = [ pkgs.stdenv.cc.cc.lib ];
          dontUnpack = true;
          dontConfigure = true;
          dontBuild = true;
          installPhase = ''
            runHook preInstall
            mkdir -p "$out/bin"
            gzip -dc "$src" > "$out/bin/tree-sitter"
            chmod +x "$out/bin/tree-sitter"
            runHook postInstall
          '';
        };

      treeSitterCustomGrammar =
        pkgs: python: grammar:
        pkgs.stdenv.mkDerivation {
          pname = "trailmark-tree-sitter-${grammar}";
          version = pyproject.project.version;

          src = ./src/trailmark/tree_sitter_custom/${grammar};

          dontConfigure = true;

          nativeBuildInputs = [
            pkgs.stdenv.cc
          ];

          buildPhase = ''
            runHook preBuild

            ext_suffix="$(${python.interpreter} -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX") or ".so")')"
            darwin_flags=(${pkgs.lib.optionalString pkgs.stdenv.hostPlatform.isDarwin "-undefined dynamic_lookup"})

            cc -shared -fPIC -O2 -std=c11 "''${darwin_flags[@]}" \
              -I"${python}/include/${python.libPrefix}" \
              -I"$src/src" \
              "$src/binding.c" \
              "$src/src/parser.c" \
              -o "_binding$ext_suffix"

            runHook postBuild
          '';

          installPhase = ''
            runHook preInstall

            mkdir -p "$out"
            cp _binding.* "$out/"

            runHook postInstall
          '';
        };

      pythonSets = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python3;

          trailmarkOverlay =
            final: prev:
            let
              grammarPython = final.mkVirtualEnv "trailmark-grammar-build-env" prev.trailmark.passthru.dependencies;
            in
            {
              trailmark = prev.trailmark.overrideAttrs (old: {
                nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
                  (treeSitterCli pkgs)
                  pkgs.nodejs
                  pkgs.stdenv.cc
                ];
                postInstall = (old.postInstall or "") + ''
                  # Bindings include a digest of their installed path and source files.
                  # Build them before the Nix store becomes read-only.
                  for grammar in circom masm verus; do
                    PYTHONPATH="$out/${final.python.sitePackages}" ${grammarPython}/bin/python \
                      -c "from trailmark.tree_sitter_custom.$grammar import language; language()"
                  done
                '';
              });
            };
        in
        (pkgs.callPackage pyproject-nix.build.packages {
          inherit python;
        }).overrideScope
          (
            lib.composeManyExtensions [
              pyproject-build-systems.overlays.default
              overlay
              trailmarkOverlay
            ]
          )
      );
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python3;
          tree-sitter-circom = treeSitterCustomGrammar pkgs python "circom";
          tree-sitter-masm = treeSitterCustomGrammar pkgs python "masm";
          parserBundles = {
            x86_64-linux = {
              platform = "linux-x86_64";
              sha256 = "4ad211e4a265be2106909ef9db4ccdc706e557436a446fbc10afbf23d668a2a5";
            };
            aarch64-darwin = {
              platform = "macos-arm64";
              sha256 = "9931ad24c0714c6ebe73554f8ad981928902d3d4ea0b2bcd6365078591f47705";
            };
          };
          bundle = parserBundles.${system};
          version = pythonSets.${system}.tree-sitter-language-pack.version;
          parserArchive = pkgs.fetchurl {
            url = "https://github.com/xberg-io/tree-sitter-language-pack/releases/download/v${version}/parsers-${bundle.platform}.tar.zst";
            inherit (bundle) sha256;
          };
          parserLibraries =
            pkgs.runCommand "trailmark-parsers-${version}"
              {
                nativeBuildInputs = [
                  pkgs.zstd
                ]
                ++ lib.optionals pkgs.stdenv.hostPlatform.isLinux [ pkgs.autoPatchelfHook ];
                buildInputs = [ pkgs.stdenv.cc.cc.lib ];
              }
              ''
                mkdir -p "$out"
                tar --zstd -xf ${parserArchive} -C "$out"
              '';
          env =
            (pythonSets.${system}.mkVirtualEnv "trailmark-env" workspace.deps.default).overrideAttrs
              (old: {
                # Configure both the CLI and Python library without patching dependencies.
                postInstall = (old.postInstall or "") + ''
                  echo 'import os; os.environ.setdefault("TREE_SITTER_LANGUAGE_PACK_LIBS_DIR", "${parserLibraries}")' > "$out/${python.sitePackages}/trailmark-parsers.pth"
                '';
              });
        in
        {
          inherit tree-sitter-circom tree-sitter-masm;
          trailmark = env;
          default = env;
        }
      );

      checks = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          trailmark = self.packages.${system}.trailmark;
        in
        {
          inherit trailmark;
          trailmark-self =
            pkgs.runCommand "trailmark-self-check"
              {
                nativeBuildInputs = [ trailmark ];
              }
              ''
                export HOME=$(mktemp -d)
                trailmark analyze ${self} --summary | tee "$out"
                grep -q "Nodes: " "$out"
                trailmark analyze ${self} > /dev/null
                trailmark entrypoints ${self} > /dev/null
                python -c "from trailmark.query.api import QueryEngine; engine = QueryEngine.from_directory('${self}/src', language='auto'); assert engine.summary()['total_nodes'] > 0"
                python -c "from trailmark.query.api import QueryEngine; assert all(QueryEngine.from_directory('${self}/tests/fixtures/kat/' + lang, language=lang).summary()['total_nodes'] > 0 for lang in ('circom', 'masm', 'sql', 'rust'))"
                python -c "from trailmark.tree_sitter_custom.verus import language; from tree_sitter import Language, Parser; assert not Parser(Language(language())).parse(b'verus! { spec fn value() -> int { 1 } }').root_node.has_error"
              '';
        }
      );

      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          default = pkgs.mkShell {
            packages = [
              self.packages.${system}.trailmark
              pkgs.uv
            ];
          };
        }
      );

      apps = forAllSystems (system: rec {
        trailmark = {
          type = "app";
          program = "${self.packages.${system}.trailmark}/bin/trailmark";
        };
        default = trailmark;
      });
    };
}
