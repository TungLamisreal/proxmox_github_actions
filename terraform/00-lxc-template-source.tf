data "external" "lxc_template" {
  program = [
    "python3",
    "${path.module}/../scripts/select_lxc_template.py"
  ]
}
