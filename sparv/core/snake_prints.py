"""Printing functions for Snakefile."""

from rich import box
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from sparv import util
from sparv.core import registry, snake_utils
from sparv.core.console import console


def prettyprint_yaml(in_dict):
    """Pretty-print YAML."""
    from rich.syntax import Syntax
    import yaml

    class MyDumper(yaml.Dumper):
        """Customized YAML dumper that indents lists."""

        def increase_indent(self, flow=False, indentless=False):
            """Force indentation."""
            return super(MyDumper, self).increase_indent(flow)

    # Resolve aliases and replace them with their anchors' contents
    yaml.Dumper.ignore_aliases = lambda *args: True
    yaml_str = yaml.dump(in_dict, default_flow_style=False, Dumper=MyDumper, indent=4, allow_unicode=True)
    # Print syntax highlighted
    console.print(Syntax(yaml_str, "yaml"))


def print_module_info(module_types, module_names, snake_storage, reverse_config_usage):
    """Wrap module printing functions: print correct info for chosen module_types and module_names."""
    all_module_types = {
        "annotators": ("annotators", snake_storage.all_annotations),
        "importers": ("importers", snake_storage.all_importers),
        "exporters": ("exporters", snake_storage.all_exporters),
        "custom_annotators": ("custom annotators", snake_storage.all_custom_annotators)
    }

    if not module_types:
        module_types = all_module_types.keys()

    module_names = [n.lower() for n in module_names]

    # Print module info for all chosen module_types
    if not module_names:
        for m in module_types:
            module_type, modules = all_module_types.get(m)
            print_modules(modules, module_type, reverse_config_usage, snake_storage)

    # Print only info for chosen module_names
    else:
        invalid_modules = module_names
        for m in module_types:
            module_type, modules = all_module_types.get(m)
            modules = dict((k, v) for k, v in modules.items() if k in module_names)
            if modules:
                invalid_modules = [m for m in invalid_modules if m not in modules.keys()]
                print_modules(modules, module_type, reverse_config_usage, snake_storage)
        if invalid_modules:
            console.print("[red]Module{} not found: {}[/red]".format("s" if len(invalid_modules) > 1 else "",
                                                                     ", ".join(invalid_modules)))


def print_modules(modules: dict, module_type: str, reverse_config_usage: dict, snake_storage: snake_utils.SnakeStorage,
                  print_params: bool = False):
    """Print module information."""
    custom_annotations = snake_storage.all_custom_annotators

    # Box styles
    left_line = box.Box("    \n┃   \n┃   \n┃   \n┃   \n┃   \n┃   \n    ")
    minimal = box.Box("    \n  │ \n╶─┼╴\n  │ \n╶─┼╴\n╶─┼╴\n  │ \n    \n")
    box_style = minimal

    # Module type header
    print()
    console.print(f"  [b]{module_type.upper()}[/b]", style="reverse", justify="left")  # 'justify' to fill entire width
    print()

    for i, module_name in enumerate(sorted(modules)):
        if i:
            console.print(Rule())

        # Module name header
        console.print(f"\n[bright_black]:[/][dim]:[/]: [b]{module_name.upper()}[/b]\n")

        if registry.modules[module_name].description:
            console.print(Padding(registry.modules[module_name].description, (0, 4, 1, 4)))

        for f_name in sorted(modules[module_name]):
            # Function name and description
            f_desc = modules[module_name][f_name]["description"]
            console.print(Padding(Panel(f"[b]{f_name.upper()}[/b]\n[i]{f_desc}[/i]", box=left_line, padding=(0, 1),
                                        border_style="bright_green"), (0, 2)))

            # Annotations
            f_anns = modules[module_name][f_name].get("annotations", {})
            if f_anns:
                this_box_style = box_style if any(a[1] for a in f_anns) else box.SIMPLE
                table = Table(title="[b]Annotations[/b]", box=this_box_style, show_header=False,
                              title_justify="left", padding=(0, 2), pad_edge=False, border_style="bright_black")
                table.add_column(no_wrap=True)
                table.add_column()
                for f_ann in sorted(f_anns):
                    table.add_row("• " + f_ann[0].name + (
                        f"\n  [i dim]class:[/] <{f_ann[0].cls}>" if f_ann[0].cls else ""),
                        f_ann[1] or "")
                console.print(Padding(table, (0, 0, 0, 4)))

            # Config variables
            f_config = reverse_config_usage.get(f"{module_name}:{f_name}")
            if f_config:
                console.print()
                table = Table(title="[b]Configuration variables used[/b]", box=box_style, show_header=False,
                              title_justify="left", padding=(0, 2), pad_edge=False, border_style="bright_black")
                table.add_column(no_wrap=True)
                table.add_column()
                for config_key in sorted(f_config):
                    table.add_row("• " + config_key[0], config_key[1] or "")
                console.print(Padding(table, (0, 0, 0, 4)))

            # Always print parameters for custom annotations
            params = modules[module_name][f_name].get("params", {})
            custom_params = None
            if custom_annotations.get(module_name, {}).get(f_name):
                custom_params = custom_annotations[module_name][f_name].get("params", {})
                params = custom_params

            # Arguments
            if (print_params and params) or custom_params:
                table = Table(title="[b]Arguments[/b]", box=box_style, show_header=False, title_justify="left",
                              padding=(0, 2), pad_edge=False, border_style="bright_black")
                table.add_column(no_wrap=True)
                table.add_column()
                for p, (default, typ, li, optional) in params.items():
                    opt_str = "(optional) " if optional else ""
                    typ_str = "list of " + typ.__name__ if li else typ.__name__
                    def_str = f", default: {repr(default)}" if default is not None else ""
                    table.add_row("• " + p, f"{opt_str}{typ_str}{def_str}")
                console.print(Padding(table, (0, 0, 0, 4)))
            print()


def print_annotation_classes():
    """Print info about annotation classes."""
    print()
    table = Table(title="Available annotation classes", box=box.SIMPLE, show_header=False, title_justify="left")
    table.add_column(no_wrap=True)
    table.add_column()

    table.add_row("[b]Defined by pipeline modules[/b]")
    table.add_row("  [i]Class[/i]", "[i]Annotation[/i]")
    for cls, anns in registry.annotation_classes["module_classes"].items():
        table.add_row("  " + cls, "\n".join(anns))

    if registry.annotation_classes["config_classes"]:
        table.add_row()
        table.add_row("[b]From config[/b]")
        table.add_row("  [i]Class[/i]", "[i]Annotation[/i]")
        for cls, ann in registry.annotation_classes["config_classes"].items():
            table.add_row("  " + cls, ann)

    console.print(table)