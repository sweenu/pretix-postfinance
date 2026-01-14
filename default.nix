{
  lib,
  buildPythonPackage,
  uv-build,
}:

buildPythonPackage {
  pname = "pretix-postfinance";
  version = "1.0.0";
  pyproject = true;

  src = ./.;

  nativeBuildInputs = [
    uv-build
  ];

  # Skip tests during build (they require Django setup)
  doCheck = false;

  pythonImportsCheck = [
    "pretix_postfinance"
  ];

  meta = {
    description = "PostFinance Checkout payment plugin for pretix";
    homepage = "https://github.com/sweenu/pretix-postfinance";
    license = lib.licenses.agpl3Only;
  };
}
