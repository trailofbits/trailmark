{
  description = "Trailmark – parse source code into a queryable graph";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs = {
        nixpkgs.follows = "nixpkgs";
        pyproject-nix.follows = "pyproject-nix";
        uv2nix.follows = "uv2nix";
      };
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      uv2nix,
      pyproject-nix,
      pyproject-build-systems,
    }:
    let
      supportedSystems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];

      forAllSystems = f: nixpkgs.lib.genAttrs supportedSystems f;

      # Load the uv workspace from the lockfile.
      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };

      overlay = workspace.mkPyprojectOverlay {
        sourcePreference = "wheel";
      };

      # Build-system overlay from the registry.
      buildSystemOverlay = pyproject-build-systems.overlays.default;

      # Build a vendored tree-sitter grammar as a standalone shared object.
      mkTreeSitterGrammar =
        {
          pkgs,
          python,
          name,
          src,
        }:
        pkgs.stdenv.mkDerivation {
          pname = "tree-sitter-${name}";
          version = "0-vendored";
          inherit src;

          nativeBuildInputs = [ pkgs.stdenv.cc ];

          buildPhase =
            let
              darwinFlags = pkgs.lib.optionalString pkgs.stdenv.hostPlatform.isDarwin "-undefined dynamic_lookup";
            in
            ''
              ext_suffix=$(${python}/bin/python3 -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")
              cc -shared -fPIC -O2 -std=c11 \
                ${darwinFlags} \
                -I${python}/include/${python.libPrefix} \
                -Isrc \
                binding.c src/parser.c \
                -o _binding$ext_suffix
            '';

          installPhase = ''
            mkdir -p $out/lib
            cp _binding.* $out/lib/
          '';
        };

      # Per-package build fixes.
      fixupsOverlay = _final: prev: {
        # tree-sitter has native extensions – needs setuptools.
        tree-sitter = prev.tree-sitter.overrideAttrs (old: {
          nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
            prev.setuptools
          ];
        });
      };

      mkPkgsFor =
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python313;

          pythonSet =
            (pkgs.callPackage pyproject-nix.build.packages { inherit python; }).overrideScope
              (
                nixpkgs.lib.composeManyExtensions [
                  buildSystemOverlay
                  overlay
                  fixupsOverlay
                ]
              );

          treeSitterCircom = mkTreeSitterGrammar {
            inherit pkgs python;
            name = "circom";
            src = ./src/trailmark/tree_sitter_custom/circom;
          };

          treeSitterMasm = mkTreeSitterGrammar {
            inherit pkgs python;
            name = "masm";
            src = ./src/trailmark/tree_sitter_custom/masm;
          };
        in
        {
          inherit
            pkgs
            pythonSet
            treeSitterCircom
            treeSitterMasm
            ;
        };
    in
    {
      packages = forAllSystems (
        system:
        let
          inherit (mkPkgsFor system)
            pkgs
            pythonSet
            treeSitterCircom
            treeSitterMasm
            ;

          # The virtual environment with trailmark and all its deps.
          venv = pythonSet.mkVirtualEnv "trailmark-env" {
            trailmark = [ ];
          };
        in
        {
          default = pkgs.stdenv.mkDerivation {
            pname = "trailmark";
            version = "0.1.2";
            dontUnpack = true;

            nativeBuildInputs = [ pkgs.makeWrapper ];

            buildInputs = [
              treeSitterCircom
              treeSitterMasm
            ];

            installPhase = ''
              mkdir -p $out/bin

              # Copy the venv's trailmark site-packages so we can inject
              # pre-compiled grammars into the tree_sitter_custom dirs.
              siteDir=$out/lib/${pkgs.python313.libPrefix}/site-packages
              mkdir -p $siteDir
              cp -rL --no-preserve=mode ${venv}/lib/${pkgs.python313.libPrefix}/site-packages/* $siteDir/

              # Inject pre-compiled grammar shared objects.
              cp ${treeSitterCircom}/lib/_binding.* \
                $siteDir/trailmark/tree_sitter_custom/circom/
              cp ${treeSitterMasm}/lib/_binding.* \
                $siteDir/trailmark/tree_sitter_custom/masm/

              makeWrapper ${venv}/bin/trailmark $out/bin/trailmark \
                --set PYTHONPATH $siteDir
            '';

            meta = {
              description = "Parse source code into a queryable graph of functions, classes, calls, and semantic annotations";
              license = pkgs.lib.licenses.asl20;
              mainProgram = "trailmark";
            };
          };

          venv = venv;

          inherit treeSitterCircom treeSitterMasm;
        }
      );

      devShells = forAllSystems (
        system:
        let
          inherit (mkPkgsFor system) pkgs pythonSet;

          devVenv = pythonSet.mkVirtualEnv "trailmark-dev-env" {
            trailmark = [ "dev" ];
          };
        in
        {
          default = pkgs.mkShell {
            packages = [
              devVenv
              pkgs.uv
              pkgs.stdenv.cc
            ];

            shellHook = ''
              unset PYTHONPATH
            '';
          };
        }
      );
    };
}
