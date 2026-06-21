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
              pkgs.stdenv.cc
            ];

            pythonRelaxDeps = [
              "tree-sitter-language-pack"
            ];

            postInstall = ''
              ext_suffix="$(${python.interpreter} -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX") or ".so")')"

              build_tree_sitter_grammar() {
                local grammar="$1"
                local grammar_dir="$out/${python.sitePackages}/trailmark/tree_sitter_custom/$grammar"
                local src_dir="$grammar_dir/src"
                local output="$grammar_dir/_binding$ext_suffix"
                local darwin_flags=(${pkgs.lib.optionalString pkgs.stdenv.hostPlatform.isDarwin "-undefined dynamic_lookup"})

                cc -shared -fPIC -O2 -std=c11 "''${darwin_flags[@]}" \
                  -I"${python}/include/${python.libPrefix}" \
                  -I"$src_dir" \
                  "$grammar_dir/binding.c" \
                  "$src_dir/parser.c" \
                  -o "$output"
              }

              build_tree_sitter_grammar circom
              build_tree_sitter_grammar masm
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
          inherit trailmark;
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
