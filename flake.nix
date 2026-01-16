{
  description = "PostFinance Checkout payment plugin for pretix";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs =
    { self, nixpkgs }:
    let
      lib = nixpkgs.lib;
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: lib.genAttrs systems (system: f system);
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = import nixpkgs { inherit system; };

          pretixPython = pkgs.pretix.python;
          pyPkgs = pretixPython.pkgs;

          postfinancecheckout = pyPkgs.callPackage ./postfinancecheckout.nix { };
          pretix-plugin-build = pyPkgs.callPackage ./plugin-build.nix { };
        in
        {
          default = pyPkgs.buildPythonPackage {
            pname = "pretix-postfinance";
            version = "1.0.0";
            src = self;
            format = "pyproject";

            build-system = [
              pyPkgs.setuptools
              pretix-plugin-build
            ];

            dependencies = [ postfinancecheckout ];

            pythonImportsCheck = [ "pretix_postfinance" ];

            # doCheck = false;
          };
        }
      );

      devShells = forAllSystems (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
          pretixPython = pkgs.pretix.python;
        in
        {
          default = pkgs.mkShell {
            packages = [
              (pretixPython.withPackages (ps: [
                ps.pretix-postfinance
                ps.postfinancecheckout
                ps.pretix-plugin-build
              ]))
            ];
          };
        }
      );
    };
}
