def Struct(name, fields):
    class Bunch:
        def __init__(self, **kwds):
            self.__dict__.update(kwds)
    Bunch.__name__ = name
    for f in fields:
        Bunch.__dict__[f] = None
    return Bunch