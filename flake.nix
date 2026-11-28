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
              sha256 = "7ed56d152c52437397b9fcaa43cf341f49933cecc1ca291843f0d347c6e2cb3a";
            };
            aarch64-darwin = {
              platform = "macos-arm64";
              sha256 = "5a08b66e5713b0c69691611250b2191d07139647a3c6e39ba85954284b634d2d";
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
        let
          grammarRoot = ./src/trailmark/tree_sitter_custom;
        in
        pkgs.stdenv.mkDerivation {
          pname = "trailmark-tree-sitter-${grammar}";
          version = pyproject.project.version;

          src = lib.fileset.toSource {
            root = grammarRoot;
            fileset = lib.fileset.unions [
              (grammarRoot + "/binding.c")
              (grammarRoot + "/${grammar}")
            ];
          };

          dontConfigure = true;

          nativeBuildInputs = [
            pkgs.stdenv.cc
          ]
          ++ lib.optionals (grammar == "circom") [
            (treeSitterCli pkgs)
            pkgs.nodejs
          ];

          buildPhase = ''
            runHook preBuild

            ${lib.optionalString (grammar == "circom") ''(cd "${grammar}" && tree-sitter generate --abi 14)''}

            ext_suffix="$(${python.interpreter} -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX") or ".so")')"
            darwin_flags=(${pkgs.lib.optionalString pkgs.stdenv.hostPlatform.isDarwin "-undefined dynamic_lookup"})

            cc -shared -fPIC -O2 -std=c11 "''${darwin_flags[@]}" \
              -I"${python}/include/${python.libPrefix}" \
              -I"${grammar}/src" \
              -DTRAILMARK_LANGUAGE=tree_sitter_${grammar} \
              binding.c \
              "${grammar}/src/parser.c" \
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
                passthru = old.passthru // { inherit grammarPython; };
                # The Nix package builds a wheel. Test and CI inputs belong to the checks.
                src = lib.fileset.toSource {
                  root = ./.;
                  fileset = lib.fileset.unions [
                    ./.gitignore
                    ./LICENSE
                    ./NOTICE
                    ./README.md
                    ./hatch_build.py
                    ./pyproject.toml
                    ./src
                  ];
                };
                nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
                  (treeSitterCli pkgs)
                  pkgs.nodejs
                  pkgs.stdenv.cc
                ];
                postInstall = (old.postInstall or "") + ''
                  # Bindings include a digest of their installed path and source files.
                  # Build them before the Nix store becomes read-only.
                  PYTHONPATH="$out/${final.python.sitePackages}" ${grammarPython}/bin/python \
                    -c "from trailmark.tree_sitter_custom._native import precompile_all; precompile_all()"
                '';
              });
            };
        in
        (pkgs.callPackage pyproject-nix.build.packages {
          inherit python;
          # The language-pack wheel in uv.lock targets macOS 15 or newer.
          stdenv =
            if pkgs.stdenv.hostPlatform.isDarwin then
              pkgs.stdenv.override {
                targetPlatform = pkgs.stdenv.targetPlatform // {
                  darwinSdkVersion = "15.0";
                };
              }
            else
              pkgs.stdenv;
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
          pythonSet = pythonSets.${system};
          tree-sitter-circom = treeSitterCustomGrammar pkgs python "circom";
          tree-sitter-masm = treeSitterCustomGrammar pkgs python "masm";
          parserBundles = {
            x86_64-linux = {
              platform = "linux-x86_64";
              sha256 = "edcf1bc5546f263fadfd59e648ecb06023875eee164262bb41a805e0267a0b10";
            };
            aarch64-darwin = {
              platform = "macos-arm64";
              sha256 = "034fc9ea0dcd2d27d9371ea19e7d4349e064df2c9bf2b8f35ae0e6f6382e5e64";
            };
          };
          bundle = parserBundles.${system};
          version = pythonSet.tree-sitter-language-pack.version;
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
            (pythonSet.mkVirtualEnv "trailmark-env" workspace.deps.default).overrideAttrs
              (old: {
                # Build the reusable inputs before CI saves its dependency cache.
                passthru = (old.passthru or { }) // {
                  ciDependencies = pkgs.linkFarmFromDrvs "trailmark-ci-dependencies" (
                    [
                      pythonSet.trailmark.grammarPython
                      parserLibraries
                      pkgs.uv
                    ]
                    ++ pythonSet.trailmark.nativeBuildInputs
                    ++ pythonSet.trailmark.buildInputs
                    ++ old.nativeBuildInputs
                  );
                };
                # Load packaged libraries directly and honor an explicit download cache.
                postInstall = (old.postInstall or "") + ''
                  echo 'import os; os.environ.setdefault("TREE_SITTER_LANGUAGE_PACK_LIBS_DIR", "${parserLibraries}") if "TREE_SITTER_LANGUAGE_PACK_CACHE_DIR" not in os.environ else None' > "$out/${python.sitePackages}/trailmark-parsers.pth"
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
          inherit (self.packages.${system}) trailmark tree-sitter-circom tree-sitter-masm;
        in
        {
          inherit trailmark tree-sitter-circom tree-sitter-masm;
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
                python -c "from trailmark.query.api import QueryEngine; assert all(QueryEngine.from_directory('${self}/tests/fixtures/kat/' + lang, language=lang).summary()['total_nodes'] > 0 for lang in ('circom', 'masm', 'move', 'sql', 'rust'))"
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
          meta.description = "Trailmark command-line interface";
        };
        default = trailmark;
      });
    };
}
