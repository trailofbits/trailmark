{
  description = "Trailmark source-code graph analysis toolkit";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs, ... }:
    let
      systems = [
        "aarch64-darwin"
        # in theory others are supported, not tested so
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
          python = pkgs.python312;
          pythonPackages = python.pkgs;
          pyproject = builtins.fromTOML (builtins.readFile ./pyproject.toml);

          treeSitterCustomGrammar =
            grammar:
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

          tree-sitter-circom = treeSitterCustomGrammar "circom";
          tree-sitter-masm = treeSitterCustomGrammar "masm";

          trailmark = pythonPackages.buildPythonApplication {
            pname = "trailmark";
            version = pyproject.project.version;
            pyproject = true;

            src = ./.;

            build-system = [
              pythonPackages.hatchling
            ];

            dependencies = with pythonPackages; [
              rustworkx
              tree-sitter
              tree-sitter-language-pack
            ];

            nativeBuildInputs = [
              pythonPackages.pythonRelaxDepsHook
            ];

            pythonRelaxDeps = [
              "tree-sitter-language-pack"
            ];

            postInstall = ''
              cp ${tree-sitter-circom}/_binding.* "$out/${python.sitePackages}/trailmark/tree_sitter_custom/circom/"
              cp ${tree-sitter-masm}/_binding.* "$out/${python.sitePackages}/trailmark/tree_sitter_custom/masm/"
            '';

            pythonImportsCheck = [
              "trailmark"
              "trailmark.cli"
            ];

            meta = {
              description = "Parse source code into queryable graphs for security analysis";
              homepage = "https://github.com/trailofbits/trailmark";
              license = pkgs.lib.licenses.asl20;
              mainProgram = "trailmark";
            };
          };
        in
        {
          inherit trailmark tree-sitter-circom tree-sitter-masm;
          default = trailmark;
        }
      );

      apps = forAllSystems (
        system:
        let
          trailmark = nixpkgs.lib.getExe self.packages.${system}.trailmark;
        in
        {
          trailmark = {
            type = "app";
            program = trailmark;
          };
          default = {
            type = "app";
            program = trailmark;
          };
        }
      );
    };
}
