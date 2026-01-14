{
  description = "PostFinance Checkout payment plugin for pretix";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python312;
      in
      {
        packages.default = python.pkgs.buildPythonPackage {
          pname = "pretix-postfinance";
          version = "1.0.0";
          pyproject = true;

          src = ./.;

          nativeBuildInputs = with python.pkgs; [
            uv-build
          ];

          # Skip tests during build (they require Django setup)
          doCheck = false;

          pythonImportsCheck = [ "pretix_postfinance" ];

          meta = {
            description = "PostFinance Checkout payment plugin for pretix";
            homepage = "https://github.com/sweenu/pretix-postfinance";
            license = pkgs.lib.licenses.agpl3Only;
          };
        };

        packages.pretix-postfinance = self.packages.${system}.default;

        devShells.default = pkgs.mkShell {
          buildInputs = [
            python
            python.pkgs.uv
          ];

          shellHook = ''
            echo "pretix-postfinance development environment"
            echo "Run: uv pip install -e \".[dev]\" to install dependencies"
            echo ""
            echo "Available commands:"
            echo "  uv run ruff check ."
            echo "  uv run mypy pretix_postfinance/"
            echo "  uv run pytest tests/"
          '';
        };
      }
    );
}
